"""
HL7 ACK/NACK generation utility
"""
from typing import Optional
from datetime import datetime

from services.hl7_parser import HL7Parser


def _ts() -> str:
    return datetime.utcnow().strftime('%Y%m%d%H%M%S')


def generate_ack(raw_message: str, code: str = 'AA', error_text: Optional[str] = None, profile: str = 'default') -> str:
    """Generate a simple HL7 ACK string for an incoming message.

    - code: 'AA' (accept), 'AE' (error), or 'AR' (reject)
    - error_text: optional ERR-8 text
    - profile: reserved for future customizations
    """
    parser = HL7Parser()
    try:
        parsed = parser.parse_message(raw_message)
        sending_app = parsed.receiving_application or ''
        sending_fac = getattr(parsed, 'receiving_facility', '') or ''
        recv_app = parsed.sending_application or ''
        recv_fac = getattr(parsed, 'sending_facility', '') or ''
        version = parsed.hl7_version or '2.5'
        orig_msh10 = parsed.message_control_id or ''
    except Exception:
        # Fallback if parse fails: minimal ACK targeting unknown
        sending_app = ''
        sending_fac = ''
        recv_app = ''
        recv_fac = ''
        version = '2.5'
        orig_msh10 = ''

    # Build MSH
    field_sep = '|'
    comp = '^~\\&'
    msh = [
        'MSH', field_sep, comp, recv_app, recv_fac, sending_app, sending_fac,
        _ts(), '', 'ACK', _ts(), 'P', version
    ]
    # Build MSA
    msa = ['MSA', code, orig_msh10]
    # Optional ERR
    segments = ['|'.join(msh), '|'.join(msa)]
    if code in ('AE', 'AR') and error_text:
        err_fields = ['ERR', '', '', '', '', '', '', error_text]
        segments.append('|'.join(err_fields))

    return '\r'.join(segments) + '\r'

