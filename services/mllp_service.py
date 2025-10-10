"""
MLLP (TCP) Listener Service
---------------------------
Optional background service that listens for HL7 v2 messages over MLLP framing
and stores them for the configured tenant/vendor. Config is read from system
settings key 'mllp_config' via settings_service or from environment variables.
"""
import asyncio
import os
import ssl
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from services.hl7_parser import HL7Parser
from services.hl7_ack import generate_ack
from services.settings_service import settings_service
from models.tenant import TenantRepository
from models.vendor_endpoint import VendorEndpointRepository
from models.hl7_message import HL7MessageRepository, MessageStatus, MessageDirection


class MLLPService:
    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self._task: Optional[asyncio.Task] = None
        self._config: Dict[str, Any] = {}
        self._parser = HL7Parser()
        self._host: Optional[str] = None
        self._port: Optional[int] = None
        self._tls_enabled: bool = False
        self._metrics: Dict[str, Any] = {
            'connections_total': 0,
            'connections_active': 0,
            'messages_total': 0,
            'last_client': None,
            'last_client_verified': None,
            'last_error': None,
            'last_error_at': None,
        }

    async def start(self) -> None:
        cfg = await self._load_config()
        if not cfg.get('enabled'):
            return
        host = cfg.get('host') or '0.0.0.0'
        port = int(cfg.get('port') or 2575)
        self._config = cfg
        self._host = host
        self._port = port
        self._tls_enabled = bool(cfg.get('tls_enabled'))

        ssl_ctx = None
        if self._tls_enabled:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            cert_file = cfg.get('tls_cert_file')
            key_file = cfg.get('tls_key_file')
            ca_file = cfg.get('tls_ca_file')
            require_client = bool(cfg.get('require_client_cert'))
            if cert_file and key_file:
                ssl_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
            if ca_file:
                ssl_ctx.load_verify_locations(cafile=ca_file)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_REQUIRED if require_client else ssl.CERT_NONE

        self._server = await asyncio.start_server(self._handle_client, host, port, ssl=ssl_ctx)
        # Keep serving in background
        self._task = asyncio.create_task(self._server.serve_forever())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
            self._task = None

    async def _load_config(self) -> Dict[str, Any]:
        # Defaults from env
        env_enabled = os.getenv('MLLP_ENABLED', 'false').lower() == 'true'
        cfg = {
            'enabled': env_enabled,
            'host': os.getenv('MLLP_HOST', '0.0.0.0'),
            'port': int(os.getenv('MLLP_PORT', '2575')),
            'ack_mode': os.getenv('MLLP_ACK_MODE', 'auto'),  # none|auto|accept
            'tenant_id': os.getenv('MLLP_TENANT_ID'),
            'tenant_slug': os.getenv('MLLP_TENANT_SLUG'),
            'vendor_slug': os.getenv('MLLP_VENDOR_SLUG'),
            'tls_enabled': os.getenv('MLLP_TLS_ENABLED', 'false').lower() == 'true',
            'tls_cert_file': os.getenv('MLLP_TLS_CERT'),
            'tls_key_file': os.getenv('MLLP_TLS_KEY'),
            'tls_ca_file': os.getenv('MLLP_TLS_CA'),
            'require_client_cert': os.getenv('MLLP_TLS_REQUIRE_CLIENT', 'false').lower() == 'true',
        }
        try:
            stored = await settings_service.get_system_setting('mllp_config', None)
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if v is not None})
        except Exception:
            pass
        return cfg

    def get_status(self) -> Dict[str, Any]:
        return {
            'running': self._server is not None,
            'host': self._host,
            'port': self._port,
            'tls_enabled': self._tls_enabled,
            'mtls': {
                'required': bool(self._config.get('require_client_cert')),
                'last_client_verified': self._metrics.get('last_client_verified')
            }
        }

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            # track connection
            peer = writer.get_extra_info('peername')
            self._metrics['connections_total'] += 1
            self._metrics['connections_active'] += 1
            self._metrics['last_client'] = f"{peer[0]}:{peer[1]}" if isinstance(peer, tuple) else str(peer)
            try:
                sslobj = writer.get_extra_info('ssl_object')
                self._metrics['last_client_verified'] = bool(sslobj and sslobj.getpeercert())
            except Exception:
                self._metrics['last_client_verified'] = None

            data = await self._read_mllp_message(reader)
            if not data:
                writer.close()
                await writer.wait_closed()
                return

            raw = data.decode(errors='ignore')

            # Resolve tenant and vendor
            tenant = None
            if self._config.get('tenant_id'):
                try:
                    import uuid as _uuid
                    tenant = await TenantRepository.get_tenant_by_id(_uuid.UUID(str(self._config['tenant_id'])))
                except Exception:
                    tenant = None
            if not tenant and self._config.get('tenant_slug'):
                tenant = await TenantRepository.get_tenant_by_slug(str(self._config['tenant_slug']))
            if not tenant:
                # No tenant configured; drop with optional NACK
                await self._maybe_send_ack(raw, writer, code='AR', error_text='No tenant configured')
                return

            vendor_endpoint = None
            if self._config.get('vendor_slug'):
                vendor_endpoint = await VendorEndpointRepository.get_endpoint_by_slug(tenant['id'], str(self._config['vendor_slug']))

            # Parse and validate
            try:
                parsed = self._parser.parse_message(raw)
                validation_errors = self._parser.validate_message(parsed)
            except Exception as e:
                validation_errors = [f'Parse error: {str(e)}']
                parsed = None

            # Dedup by MSH-10 if possible, honoring tenant ingestion settings
            message_control_id = getattr(parsed, 'message_control_id', None) if parsed else None
            # Load tenant ingestion settings
            ingestion = {}
            try:
                settings = tenant.get('settings') or {}
                if isinstance(settings, str):
                    import json as _json
                    settings = _json.loads(settings)
                ingestion = (settings or {}).get('ingestion', {}) or {}
            except Exception:
                ingestion = {}
            dedup_window = int(ingestion.get('dedup_window_minutes') or 1440)
            dedup_action = str(ingestion.get('dedup_action') or 'IGNORE').upper()

            if message_control_id:
                existing = await HL7MessageRepository.find_recent_by_control_id(tenant['id'], message_control_id, window_minutes=dedup_window)
                if existing:
                    if dedup_action == 'ERROR':
                        await self._maybe_send_ack(raw, writer, code='AE', error_text='Duplicate message')
                        return
                    elif dedup_action == 'STORE_IGNORED':
                        await HL7MessageRepository.create_message(
                            tenant_id=tenant['id'],
                            raw_message=raw,
                            message_type=(getattr(parsed, 'message_type', None) or 'UNKNOWN') if parsed else 'UNKNOWN',
                            message_control_id=message_control_id,
                            status=MessageStatus.IGNORED.value,
                            direction=MessageDirection.INBOUND.value,
                            vendor_endpoint_id=(vendor_endpoint or {}).get('id') if vendor_endpoint else None,
                        )
                        await self._maybe_send_ack(raw, writer, code='AA')
                        return
                    else:
                        # IGNORE: send success ACK referencing original
                        await self._maybe_send_ack(raw, writer, code='AA')
                        return

            # Store message
            await HL7MessageRepository.create_message(
                tenant_id=tenant['id'],
                raw_message=raw,
                message_type=(getattr(parsed, 'message_type', None) or 'UNKNOWN') if parsed else 'UNKNOWN',
                event_type=getattr(parsed, 'event_type', None) if parsed else None,
                hl7_version=getattr(parsed, 'hl7_version', None) if parsed else None,
                message_control_id=message_control_id,
                sending_application=getattr(parsed, 'sending_application', None) if parsed else None,
                receiving_application=getattr(parsed, 'receiving_application', None) if parsed else None,
                vendor_endpoint_id=(vendor_endpoint or {}).get('id') if vendor_endpoint else None,
                status=MessageStatus.RECEIVED.value,
                direction=MessageDirection.INBOUND.value,
            )
            self._metrics['messages_total'] += 1

            # ACK behavior
            if str(self._config.get('ack_mode') or 'auto').lower() == 'auto':
                code = 'AA' if not validation_errors else 'AE'
                err = validation_errors[0] if validation_errors else None
                await self._maybe_send_ack(raw, writer, code=code, error_text=err)
            elif str(self._config.get('ack_mode')).lower() == 'accept':
                await self._maybe_send_ack(raw, writer, code='AA')

        except Exception:
            try:
                # Attempt to NACK if a problem occurred while reading/processing
                await self._maybe_send_ack('', writer, code='AR', error_text='Processing error')
            except Exception:
                pass
            # record error
            try:
                self._metrics['last_error'] = 'processing_error'
                self._metrics['last_error_at'] = datetime.utcnow().isoformat() + 'Z'
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if self._metrics['connections_active'] > 0:
                self._metrics['connections_active'] -= 1

    async def _read_mllp_message(self, reader: asyncio.StreamReader) -> Optional[bytes]:
        # Read until we hit FS CR (0x1c 0x0d), with message started by 0x0b
        start_found = False
        buf = bytearray()
        while True:
            chunk = await reader.read(1024)
            if not chunk:
                break
            for b in chunk:
                if not start_found:
                    if b == 0x0b:
                        start_found = True
                        continue
                    else:
                        # ignore bytes until start
                        continue
                else:
                    if b == 0x1c:
                        # Peek next byte for CR
                        next_byte = await reader.read(1)
                        if next_byte and next_byte[0] == 0x0d:
                            return bytes(buf)
                        else:
                            # Unexpected byte; include and continue
                            buf.extend(bytes([b]))
                            if next_byte:
                                buf.extend(next_byte)
                    else:
                        buf.append(b)
        return None

    async def _maybe_send_ack(self, raw: str, writer: asyncio.StreamWriter, code: str = 'AA', error_text: Optional[str] = None) -> None:
        try:
            ack = generate_ack(raw, code=code, error_text=error_text)
            framed = b"\x0b" + ack.encode() + b"\x1c\x0d"
            writer.write(framed)
            await writer.drain()
        except Exception:
            pass

    def get_metrics(self) -> Dict[str, Any]:
        return dict(self._metrics)


mllp_service = MLLPService()
