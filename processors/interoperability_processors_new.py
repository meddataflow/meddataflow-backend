"""
Interoperability activity processors - Main entry point
This module imports and re-exports all message format processors.
Refactored for maintainability - each message format has its own module.
"""

# Import FHIR processors
from processors.fhir_processor import (
    process_fhir_parser_activity,
    process_fhir_transformer_activity,
    process_fhir_translator_activity,
    process_fhir_sender_activity,
)

# Import DICOM processors
from processors.dicom_processor import (
    process_dicom_parser_activity,
    process_dicom_transformer_activity,
    process_dicom_translator_activity,
    process_dicom_sender_activity,
)

# Import X12 processors
from processors.x12_processor import (
    process_x12_parser_activity,
    process_x12_transformer_activity,
    process_x12_translator_activity,
    process_x12_sender_activity,
)

# Import NCPDP processors
from processors.ncpdp_processor import (
    process_ncpdp_parser_activity,
    process_ncpdp_transformer_activity,
    process_ncpdp_translator_activity,
    process_ncpdp_sender_activity,
)

# Import Clinical Document processors (CDA, CCD, CCR)
from processors.clinical_document_processor import (
    process_cda_parser_activity,
    process_cda_transformer_activity,
    process_cda_translator_activity,
    process_cda_sender_activity,
    process_ccd_parser_activity,
    process_ccd_transformer_activity,
    process_ccd_translator_activity,
    process_ccd_sender_activity,
    process_ccr_parser_activity,
    process_ccr_transformer_activity,
    process_ccr_translator_activity,
    process_ccr_sender_activity,
)

# Re-export everything for backward compatibility
__all__ = [
    # FHIR
    "process_fhir_parser_activity",
    "process_fhir_transformer_activity",
    "process_fhir_translator_activity",
    "process_fhir_sender_activity",
    # DICOM
    "process_dicom_parser_activity",
    "process_dicom_transformer_activity",
    "process_dicom_translator_activity",
    "process_dicom_sender_activity",
    # X12
    "process_x12_parser_activity",
    "process_x12_transformer_activity",
    "process_x12_translator_activity",
    "process_x12_sender_activity",
    # NCPDP
    "process_ncpdp_parser_activity",
    "process_ncpdp_transformer_activity",
    "process_ncpdp_translator_activity",
    "process_ncpdp_sender_activity",
    # CDA
    "process_cda_parser_activity",
    "process_cda_transformer_activity",
    "process_cda_translator_activity",
    "process_cda_sender_activity",
    # CCD
    "process_ccd_parser_activity",
    "process_ccd_transformer_activity",
    "process_ccd_translator_activity",
    "process_ccd_sender_activity",
    # CCR
    "process_ccr_parser_activity",
    "process_ccr_transformer_activity",
    "process_ccr_translator_activity",
    "process_ccr_sender_activity",
]
