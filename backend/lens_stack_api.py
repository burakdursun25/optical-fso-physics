"""Lens yığını uç noktaları (POST /api/lens/stack, GET /api/lens/stack/presets).

backend/app.py dosyasının en sonunda ``app.include_router(lens_stack_router)`` ile bağlanır.
"""

import logging
import math
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend import lens_stack

logger = logging.getLogger(__name__)
lens_stack_router = APIRouter()


class StackElementParams(BaseModel):
    shape: str = "plano_convex"
    material: str = "n_bk7"
    focalLengthM: float = Field(0.1, gt=0.0, le=10.0)
    diameterM: float = Field(0.025, gt=0.0, le=1.0)
    centerThicknessM: Optional[float] = Field(None, gt=0.0, le=1.0)
    coating: str = "none"
    filter: Optional[str] = "none"
    # bir sonraki elemanın ön tepe noktasına hava boşluğu (son elemanda yok sayılır);
    # negatif değer ve çakışma Türkçe 422 mesajıyla reddedilir
    spacingM: float = 0.0


class LensStackParams(BaseModel):
    elements: Optional[List[StackElementParams]] = None   # sayı denetimi (1..8) Türkçe mesajla yapılır
    preset: Optional[str] = None
    wavelengthNm: float = Field(1550.0, ge=200.0, le=20000.0)
    linkDistanceM: float = Field(1000.0, gt=0.0, le=1e6)
    beamW0M: float = Field(0.002, gt=0.0, le=1.0)
    bareRxRadiusM: float = Field(0.0125, gt=0.0, le=10.0)
    sourceOffsetM: float = Field(0.0, ge=0.0, le=1e3)
    m2: float = Field(1.0, ge=1.0, le=20.0)
    nPoints: int = Field(61, ge=41, le=201)
    nRays: int = Field(9, ge=3, le=21)
    fieldAngleDeg: float = Field(0.0, ge=-20.0, le=20.0)


def _run(p: LensStackParams) -> dict:
    lam = p.wavelengthNm * 1e-9
    if p.elements is not None:
        els = [e.model_dump() for e in p.elements]
    elif p.preset:
        els = lens_stack.preset_elements(p.preset, lam)
    else:
        raise lens_stack.StackError("'elements' listesi veya 'preset' gerekli.")
    return lens_stack.design_stack(
        els, lam, n_points=p.nPoints, n_rays=p.nRays, field_angle_deg=p.fieldAngleDeg,
        link_distance_m=p.linkDistanceM, beam_w0_m=p.beamW0M, bare_rx_radius_m=p.bareRxRadiusM,
        source_offset_m=p.sourceOffsetM, m2=p.m2)


@lens_stack_router.post("/api/lens/stack")
def lens_stack_design(p: LensStackParams):
    try:
        return _run(p)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.error("Lens stack request failure", exc_info=(type(error), error, error.__traceback__))
        raise HTTPException(status_code=500, detail="Internal server error") from error


@lens_stack_router.get("/api/lens/stack/presets")
def lens_stack_presets(wavelengthNm: float = 1550.0):
    try:
        if not (200.0 <= wavelengthNm <= 20000.0) or not math.isfinite(wavelengthNm):
            raise ValueError("wavelengthNm 200–20000 aralığında olmalı.")
        return {"maxElements": lens_stack.MAX_ELEMENTS, "wavelengthNm": wavelengthNm,
                "presets": lens_stack.preset_list(wavelengthNm * 1e-9)}
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        logger.error("Lens stack presets failure", exc_info=(type(error), error, error.__traceback__))
        raise HTTPException(status_code=500, detail="Internal server error") from error
