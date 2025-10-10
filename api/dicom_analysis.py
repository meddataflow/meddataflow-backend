"""
AI-powered DICOM Image Analysis Endpoint
Uses Claude 3.5 Sonnet with vision capabilities to analyze medical images
"""
import io
import os
import base64
from typing import Dict, Any, Optional
from datetime import datetime
import uuid
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
import httpx

from api.auth_deps import get_current_user, get_current_tenant
from database.connection import fetch_one
from services.settings_service import settings_service

router = APIRouter(prefix="/api/interop/messages", tags=["dicom-analysis"])
logger = logging.getLogger(__name__)

class DicomImageAnalysisRequest(BaseModel):
    message_id: str

class DicomImageAnalysisResponse(BaseModel):
    success: bool
    analysis: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

@router.post("/{message_id}/analyze-dicom", response_model=DicomImageAnalysisResponse)
async def analyze_dicom_image(
    message_id: str,
    current_user: Dict[str, Any] = Depends(get_current_user),
    current_tenant: Dict[str, Any] = Depends(get_current_tenant)
):
    """Use AI (Claude with vision) to analyze a DICOM medical image and provide detailed clinical insights"""
    try:
        from PIL import Image
        import numpy as np

        # Try to import pydicom
        try:
            import pydicom
        except ImportError:
            return DicomImageAnalysisResponse(
                success=False,
                error="pydicom not installed. Install with: pip install pydicom"
            )

        message_uuid = uuid.UUID(message_id)

        # Get DICOM binary from database
        query = """
        SELECT binary_payload, message_format, file_name, tenant_id
        FROM hl7_messages
        WHERE id = $1
        """
        result = await fetch_one(query, message_uuid)

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found"
            )

        # Verify tenant ownership
        if str(result['tenant_id']) != str(current_tenant['id']):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied"
            )

        # Verify it's a DICOM file
        if result.get('message_format') != 'dicom':
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Message is not a DICOM file"
            )

        binary_payload = result.get('binary_payload')
        if not binary_payload:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="DICOM file data not found"
            )

        # Parse DICOM to get metadata
        dataset = pydicom.dcmread(io.BytesIO(bytes(binary_payload)), force=True)

        # Extract key metadata for context
        modality = getattr(dataset, 'Modality', 'Unknown')
        body_part = getattr(dataset, 'BodyPartExamined', 'Unknown')
        study_desc = getattr(dataset, 'StudyDescription', '')
        patient_age = getattr(dataset, 'PatientAge', 'Unknown')
        patient_sex = getattr(dataset, 'PatientSex', 'Unknown')
        view_position = getattr(dataset, 'ViewPosition', '')

        # Convert DICOM pixel data to PNG for Claude
        pixel_array = dataset.pixel_array

        # Handle different array shapes
        # DICOM can have shapes like (rows, cols), (frames, rows, cols), or other variations
        original_shape = pixel_array.shape

        if pixel_array.ndim == 3:
            # Check if it's (1, 1, N) - likely a misshapen 1D array that should be reshaped
            if pixel_array.shape[0] == 1 and pixel_array.shape[1] == 1:
                # This is actually a 1D array stored as 3D - try to reshape to square-ish 2D
                flat_array = pixel_array.flatten()
                # Try to make it as square as possible
                n_pixels = len(flat_array)
                rows = int(np.sqrt(n_pixels))
                cols = n_pixels // rows
                if rows * cols == n_pixels:
                    pixel_array = flat_array.reshape(rows, cols)
                else:
                    # If not perfectly square, make it work anyway
                    rows = int(np.sqrt(n_pixels))
                    cols = rows
                    pixel_array = flat_array[:rows*cols].reshape(rows, cols)
            elif pixel_array.shape[0] == 1:
                # Shape like (1, rows, cols) - squeeze the first dimension
                pixel_array = pixel_array.squeeze(0)
            elif pixel_array.shape[2] == 1:
                # Shape like (rows, cols, 1) - squeeze the last dimension
                pixel_array = pixel_array.squeeze(2)
            else:
                # Multiple frames - take middle frame
                middle_frame = pixel_array.shape[0] // 2
                pixel_array = pixel_array[middle_frame, :, :]
        elif pixel_array.ndim > 3:
            # Even more complex - flatten to 2D by taking first slice of all extra dimensions
            while pixel_array.ndim > 2:
                pixel_array = pixel_array[0]
        elif pixel_array.ndim == 1:
            # 1D array - try to reshape to square
            n_pixels = len(pixel_array)
            rows = int(np.sqrt(n_pixels))
            cols = n_pixels // rows
            if rows * cols == n_pixels:
                pixel_array = pixel_array.reshape(rows, cols)
            else:
                rows = int(np.sqrt(n_pixels))
                cols = rows
                pixel_array = pixel_array[:rows*cols].reshape(rows, cols)

        # Ensure we have a 2D array now
        if pixel_array.ndim != 2:
            raise ValueError(f"Unable to convert pixel array to 2D. Original shape: {original_shape}, Current shape: {pixel_array.shape}")

        # Normalize pixel values to 0-255 range
        pixel_min = pixel_array.min()
        pixel_max = pixel_array.max()
        if pixel_max > pixel_min:
            normalized = ((pixel_array - pixel_min) / (pixel_max - pixel_min) * 255).astype(np.uint8)
        else:
            normalized = np.zeros_like(pixel_array, dtype=np.uint8)

        # Handle MONOCHROME1 (inverted grayscale)
        photometric = getattr(dataset, 'PhotometricInterpretation', '')
        if photometric == 'MONOCHROME1':
            normalized = 255 - normalized

        # Convert to PIL Image (now guaranteed to be 2D)
        image = Image.fromarray(normalized, mode='L')

        # Resize if too large (Claude has image size limits)
        max_size = 1568  # Claude's recommended max dimension
        if image.width > max_size or image.height > max_size:
            image.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)

        # Convert to PNG bytes
        png_buffer = io.BytesIO()
        image.save(png_buffer, format='PNG')
        png_bytes = png_buffer.getvalue()
        png_base64 = base64.b64encode(png_bytes).decode('utf-8')

        # Get AI settings from settings service (same as AI workflow router)
        ai_settings = await settings_service.get_ai_settings()
        if not ai_settings.get("enabled", False):
            return DicomImageAnalysisResponse(
                success=False,
                error="AI image analysis is not enabled. Please enable AI features in admin settings."
            )

        api_key = ai_settings.get("openrouter_api_key")
        if not api_key:
            return DicomImageAnalysisResponse(
                success=False,
                error="OpenRouter API key not configured. Please configure AI settings in admin panel."
            )

        # Get model preference from settings, with fallback to specialized medical models
        # Priority: 1. GPT-4 Vision (best for medical imaging), 2. Claude 3.5 Sonnet, 3. Settings default
        available_medical_models = [
            "openai/gpt-4-vision-preview",  # OpenAI's GPT-4 with vision - excellent for medical imaging
            "anthropic/claude-3.5-sonnet",   # Claude 3.5 Sonnet - our current default
            "openai/gpt-4o",                 # GPT-4o - multimodal with good medical capabilities
        ]

        model = ai_settings.get("model", "anthropic/claude-3.5-sonnet")

        # If the configured model isn't in our medical models list, use the first available
        if model not in available_medical_models:
            model = available_medical_models[1]  # Default to Claude 3.5 Sonnet

        # Prepare the prompt for Claude
        prompt = f"""You are an expert radiologist analyzing a medical imaging study. Please provide a detailed analysis of this {modality} image.

**Patient Context:**
- Modality: {modality}
- Body Part: {body_part}
- Study: {study_desc or 'Not specified'}
- Patient Age: {patient_age}
- Patient Sex: {patient_sex}
- View: {view_position or 'Standard'}

**Analysis Requirements:**
Please provide a comprehensive analysis including:

1. **Image Quality Assessment:**
   - Evaluate the technical quality of the image
   - Note any artifacts, positioning issues, or limitations

2. **Anatomical Observations:**
   - Identify all visible anatomical structures
   - Describe their appearance, size, and position
   - Note any anatomical variants

3. **Pathological Findings:**
   - Identify any abnormalities, lesions, or pathological changes
   - Describe their characteristics (size, shape, density, location)
   - Assess severity and clinical significance
   - Note any signs of: fractures, masses, opacities, fluid collections, calcifications, etc.

4. **Differential Diagnosis:**
   - List possible diagnoses based on findings
   - Rank by likelihood
   - Explain reasoning

5. **Clinical Impression:**
   - Summarize key findings
   - Provide clinical recommendations
   - Suggest follow-up imaging if needed

6. **Urgency Assessment:**
   - Classify as: STAT/URGENT, ROUTINE, or NORMAL
   - Explain the urgency level

Please be specific, detailed, and use proper medical terminology. If the image appears normal, state that clearly."""

        # Call OpenRouter API with Claude 3.5 Sonnet (supports vision)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://meddataflow.com",
            "X-Title": "MedDataFlow DICOM Image Analyzer"
        }

        # Prepare the API payload
        # OpenRouter uses OpenAI-compatible format for all models
        # Text should come before image in the content array
        content = [
            {
                "type": "text",
                "text": prompt
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{png_base64}"
                }
            }
        ]

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": content
                }
            ],
            "max_tokens": 4096,
            "temperature": ai_settings.get("temperature", 0.3)
        }

        # Log image info for debugging
        logger.info(f"Analyzing DICOM with model: {model}")
        logger.info(f"Image dimensions: {image.width}x{image.height}")
        logger.info(f"Base64 image size: {len(png_base64)} characters")
        logger.info(f"Content structure: {len(content)} parts")
        logger.info(f"Content types in order: {[c['type'] for c in content]}")
        logger.info(f"First 100 chars of base64: {png_base64[:100]}")

        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code != 200:
                error_detail = response.text
                logger.error(f"OpenRouter API error {response.status_code}: {error_detail}")
                return DicomImageAnalysisResponse(
                    success=False,
                    error=f"OpenRouter API error: {response.status_code} - {error_detail}"
                )

            response_data = response.json()

            # Log response structure for debugging
            logger.info(f"API Response keys: {list(response_data.keys())}")

            # Extract the analysis text
            if "choices" in response_data and len(response_data["choices"]) > 0:
                analysis_text = response_data["choices"][0]["message"]["content"]
                logger.info(f"Analysis received, length: {len(analysis_text)} characters")
            else:
                logger.error(f"Unexpected API response structure: {response_data}")
                return DicomImageAnalysisResponse(
                    success=False,
                    error=f"Unexpected API response format. Please check logs."
                )

        # Structure the response
        analysis_result = {
            "modality": modality,
            "body_part": body_part,
            "study_description": study_desc,
            "patient_demographics": {
                "age": patient_age,
                "sex": patient_sex
            },
            "view_position": view_position,
            "ai_analysis": analysis_text,
            "generated_at": datetime.utcnow().isoformat(),
            "model": model,  # Include the actual model used
            "model_display_name": {
                "openai/gpt-4-vision-preview": "GPT-4 Vision (Medical Specialist)",
                "openai/gpt-4o": "GPT-4o (Multimodal)",
                "anthropic/claude-3.5-sonnet": "Claude 3.5 Sonnet"
            }.get(model, model),
            "image_dimensions": {
                "original": f"{dataset.Rows} x {dataset.Columns}",
                "analyzed": f"{image.width} x {image.height}"
            }
        }

        return DicomImageAnalysisResponse(
            success=True,
            analysis=analysis_result
        )

    except ImportError as e:
        return DicomImageAnalysisResponse(
            success=False,
            error=f"Missing required library: {str(e)}. Install with: pip install pydicom pillow numpy"
        )
    except Exception as e:
        return DicomImageAnalysisResponse(
            success=False,
            error=f"Failed to analyze DICOM image: {str(e)}"
        )
