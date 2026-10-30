from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
import httpx
from http_client import get_nse_client
import logging
import time

from auth.nse_auth import get_auth_headers
from auth.dependencies import require_auth_payload, assert_token_owns_client_code
from config import settings
from schemas.misc_reports import (
    SecuritiesDeliveryRequest,
    TwoFAReportRequest,
    ClientAuthorizationRequest,
    ClientDetailReportRequest,
    OrderLifecycleRequest,
    TransactionDetailRequest,
    FATCAReportRequest,
    AOFImageUploadReportRequest,
    ELOGUploadReportRequest,
    SIPRegReportRequest,
    SIPCanReportRequest,
    SIPInstDueReportRequest,
    SIPTopupReportRequest,
    StepupRegReportRequest,
    XSIPRegReportRequest,
    XSIPCanReportRequest,
    XSIPInstDueReportRequest,
    XSIPTopupReportRequest,
    STPRegReportRequest,
    STPCanReportRequest,
    STPInstDueReportRequest,
    SWPRegReportRequest,
    SWPCanReportRequest,
    SWPInstDueReportRequest,
    MasterDownloadRequest,
    ClientKYCReportRequest,
    GenericReportResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["Misc Reports"])

NSE_SECURITIES_DELIVERY_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SECURITIES_DELIVERY_AND_SHORTAGE_REPORT"
NSE_2FA_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/2fa"
NSE_CLIENT_AUTH_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/client_authorization"
NSE_CLIENT_DETAIL_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/CLIENT_DETAIL_REPORT"
NSE_ORDER_LIFECYCLE_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/order_lifecycle"
NSE_TRANSACTION_DETAIL_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/TRANSACTION_DETAIL_REPORT"
NSE_FATCA_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/FATCA_REPORT"
NSE_AOF_IMAGE_UPLOAD_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/AOF_IMAGE_UPLOAD_REPORT"
NSE_ELOG_UPLOAD_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/ELOG_UPLOAD_REPORT"
NSE_SIP_REG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SIP_REG_REPORT"
NSE_SIP_CAN_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SIP_CAN_REPORT"
NSE_SIP_INST_DUE_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SIP_INST_DUE_REPORT"
NSE_SIP_TOPUP_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SIP_TOPUP_REPORT"
NSE_STEPUP_REG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/STEPUP_REG_REPORT"
NSE_XSIP_REG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/XSIP_REG_REPORT"
NSE_XSIP_CAN_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/XSIP_CAN_REPORT"
NSE_XSIP_INST_DUE_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/XSIP_INST_DUE_REPORT"
NSE_XSIP_TOPUP_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/XSIP_TOPUP_REPORT"
NSE_STP_REG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/STP_REG_REPORT"
NSE_STP_CAN_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/STP_CAN_REPORT"
NSE_STP_INST_DUE_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/STP_INST_DUE_REPORT"
NSE_SWP_REG_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SWP_REG_REPORT"
NSE_SWP_CAN_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SWP_CAN_REPORT"
NSE_SWP_INST_DUE_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/SWP_INST_DUE_REPORT"
NSE_MASTER_DOWNLOAD_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/MASTER_DOWNLOAD"
NSE_CLIENT_KYC_REPORT_URL = f"{settings.NSE_BASE_URL}/nsemfdesk/api/v2/reports/CLIENT_KYC_REPORT"

_MASTER_DOWNLOAD_CACHE_TTL_SECONDS = 5 * 60
_master_download_cache: dict[str, tuple[float, bytes, str]] = {}


async def _fetch_report(url: str, payload: dict) -> dict:
    try:
        async with get_nse_client(60) as client:
            response = await client.post(url, headers=get_auth_headers(), json=payload)
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to NSE: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="NSE request timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")

    logger.info(f"NSE Response status: {response.status_code}")
    logger.info(f"NSE Response body: {response.text[:500]}")

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="NSE authentication failed")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    try:
        return response.json()
    except Exception:
        raise HTTPException(status_code=502, detail=f"NSE returned invalid JSON: {response.text[:500]}")


@router.post("/securities-delivery-shortage", response_model=GenericReportResponse)
async def securities_delivery_shortage(payload: SecuritiesDeliveryRequest):
    logger.info(f"Sending to NSE URL: {NSE_SECURITIES_DELIVERY_URL}")
    return await _fetch_report(NSE_SECURITIES_DELIVERY_URL, payload.model_dump(exclude_none=True))


@router.post("/2fa", response_model=GenericReportResponse)
async def two_fa_report(payload: TwoFAReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_2FA_URL}")
    return await _fetch_report(NSE_2FA_URL, payload.model_dump(exclude_none=True))


@router.post("/client-authorization", response_model=GenericReportResponse)
async def client_authorization(payload: ClientAuthorizationRequest):
    logger.info(f"Sending to NSE URL: {NSE_CLIENT_AUTH_URL}")
    return await _fetch_report(NSE_CLIENT_AUTH_URL, payload.model_dump(exclude_none=True))


@router.post("/client-detail", response_model=GenericReportResponse)
async def client_detail_report(payload: ClientDetailReportRequest, token_payload: dict = Depends(require_auth_payload)):
    auto_code = (token_payload.get("client_codes") or [None])[0]
    if not payload.client_code:
        if not auto_code:
            raise HTTPException(status_code=422, detail="client_code is required")
        payload.client_code = auto_code
    assert_token_owns_client_code(token_payload, payload.client_code)
    logger.info(f"Sending to NSE URL: {NSE_CLIENT_DETAIL_URL}")
    return await _fetch_report(NSE_CLIENT_DETAIL_URL, payload.model_dump(exclude_none=True))


@router.post("/order-lifecycle", response_model=GenericReportResponse)
async def order_lifecycle(payload: OrderLifecycleRequest, token_payload: dict = Depends(require_auth_payload)):
    auto_code = (token_payload.get("client_codes") or [None])[0]
    if not payload.client_code:
        if not auto_code:
            raise HTTPException(status_code=422, detail="client_code is required")
        payload.client_code = auto_code
    assert_token_owns_client_code(token_payload, payload.client_code)
    logger.info(f"Sending to NSE URL: {NSE_ORDER_LIFECYCLE_URL}")
    return await _fetch_report(NSE_ORDER_LIFECYCLE_URL, payload.model_dump(exclude_none=True))


@router.post("/transaction-detail", response_model=GenericReportResponse)
async def transaction_detail(payload: TransactionDetailRequest, token_payload: dict = Depends(require_auth_payload)):
    auto_code = (token_payload.get("client_codes") or [None])[0]
    if not payload.client_code:
        if not auto_code:
            raise HTTPException(status_code=422, detail="client_code is required")
        payload.client_code = auto_code
    assert_token_owns_client_code(token_payload, payload.client_code)
    logger.info(f"Sending to NSE URL: {NSE_TRANSACTION_DETAIL_URL}")
    return await _fetch_report(NSE_TRANSACTION_DETAIL_URL, payload.model_dump(exclude_none=True))


@router.post("/fatca", response_model=GenericReportResponse)
async def fatca_report(payload: FATCAReportRequest, token_payload: dict = Depends(require_auth_payload)):
    if not payload.client_code:
        raise HTTPException(status_code=422, detail="client_code is required")
    assert_token_owns_client_code(token_payload, payload.client_code)
    logger.info(f"Sending to NSE URL: {NSE_FATCA_REPORT_URL}")
    return await _fetch_report(NSE_FATCA_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/aof-image-upload", response_model=GenericReportResponse)
async def aof_image_upload_report(payload: AOFImageUploadReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_AOF_IMAGE_UPLOAD_REPORT_URL}")
    return await _fetch_report(NSE_AOF_IMAGE_UPLOAD_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/elog-upload", response_model=GenericReportResponse)
async def elog_upload_report(payload: ELOGUploadReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_ELOG_UPLOAD_REPORT_URL}")
    return await _fetch_report(NSE_ELOG_UPLOAD_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/sip-registration", response_model=GenericReportResponse)
async def sip_reg_report(payload: SIPRegReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SIP_REG_REPORT_URL}")
    return await _fetch_report(NSE_SIP_REG_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/sip-cancellation", response_model=GenericReportResponse)
async def sip_can_report(payload: SIPCanReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SIP_CAN_REPORT_URL}")
    return await _fetch_report(NSE_SIP_CAN_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/sip-installment-due", response_model=GenericReportResponse)
async def sip_inst_due_report(payload: SIPInstDueReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SIP_INST_DUE_REPORT_URL}")
    return await _fetch_report(NSE_SIP_INST_DUE_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/sip-topup", response_model=GenericReportResponse)
async def sip_topup_report(payload: SIPTopupReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SIP_TOPUP_REPORT_URL}")
    return await _fetch_report(NSE_SIP_TOPUP_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/stepup-registration", response_model=GenericReportResponse)
async def stepup_reg_report(payload: StepupRegReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_STEPUP_REG_REPORT_URL}")
    return await _fetch_report(NSE_STEPUP_REG_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/xsip-registration", response_model=GenericReportResponse)
async def xsip_reg_report(payload: XSIPRegReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_XSIP_REG_REPORT_URL}")
    return await _fetch_report(NSE_XSIP_REG_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/xsip-cancellation", response_model=GenericReportResponse)
async def xsip_can_report(payload: XSIPCanReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_XSIP_CAN_REPORT_URL}")
    return await _fetch_report(NSE_XSIP_CAN_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/xsip-installment-due", response_model=GenericReportResponse)
async def xsip_inst_due_report(payload: XSIPInstDueReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_XSIP_INST_DUE_REPORT_URL}")
    return await _fetch_report(NSE_XSIP_INST_DUE_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/xsip-topup", response_model=GenericReportResponse)
async def xsip_topup_report(payload: XSIPTopupReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_XSIP_TOPUP_REPORT_URL}")
    return await _fetch_report(NSE_XSIP_TOPUP_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/stp-registration", response_model=GenericReportResponse)
async def stp_reg_report(payload: STPRegReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_STP_REG_REPORT_URL}")
    return await _fetch_report(NSE_STP_REG_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/stp-cancellation", response_model=GenericReportResponse)
async def stp_can_report(payload: STPCanReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_STP_CAN_REPORT_URL}")
    return await _fetch_report(NSE_STP_CAN_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/stp-installment-due", response_model=GenericReportResponse)
async def stp_inst_due_report(payload: STPInstDueReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_STP_INST_DUE_REPORT_URL}")
    return await _fetch_report(NSE_STP_INST_DUE_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/swp-registration", response_model=GenericReportResponse)
async def swp_reg_report(payload: SWPRegReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SWP_REG_REPORT_URL}")
    return await _fetch_report(NSE_SWP_REG_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/swp-cancellation", response_model=GenericReportResponse)
async def swp_can_report(payload: SWPCanReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SWP_CAN_REPORT_URL}")
    return await _fetch_report(NSE_SWP_CAN_REPORT_URL, payload.model_dump(exclude_none=True))


@router.post("/swp-installment-due", response_model=GenericReportResponse)
async def swp_inst_due_report(payload: SWPInstDueReportRequest):
    logger.info(f"Sending to NSE URL: {NSE_SWP_INST_DUE_REPORT_URL}")
    return await _fetch_report(NSE_SWP_INST_DUE_REPORT_URL, payload.model_dump(exclude_none=True))


async def _download_master(payload: MasterDownloadRequest) -> Response:
    logger.info("Sending to NSE URL: %s", NSE_MASTER_DOWNLOAD_URL)
    try:
        async with get_nse_client(120) as client:
            response = await client.post(
                NSE_MASTER_DOWNLOAD_URL,
                headers=get_auth_headers(),
                json=payload.model_dump()
            )
    except httpx.ConnectError as e:
        raise HTTPException(status_code=502, detail=f"Cannot connect to NSE: {e}")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="NSE request timed out")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")

    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="NSE authentication failed")
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)

    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            return JSONResponse(content=response.json())
        except ValueError:
            raise HTTPException(status_code=502, detail="NSE returned invalid JSON")

    media_type = content_type.split(";", 1)[0].strip() or "text/plain"
    return Response(content=response.content, media_type=media_type)


@router.post("/master-download")
async def master_download(payload: MasterDownloadRequest):
    logger.info("Sending to NSE URL: %s", NSE_MASTER_DOWNLOAD_URL)
    started_at = time.perf_counter()
    cache_key = payload.file_type.strip().upper()
    cacheable = cache_key == "SCH"
    if cacheable:
        cached = _master_download_cache.get(cache_key)
        if cached is not None:
            expires_at, content, media_type = cached
            if time.monotonic() < expires_at:
                logger.info("Serving NSE master-download file_type=%s from cache", cache_key)
                return Response(
                    content=content,
                    media_type=media_type,
                    headers={"X-Master-Cache": "HIT"},
                )
            _master_download_cache.pop(cache_key, None)

    client = get_nse_client(120)
    try:
        request = client.build_request(
            "POST",
            NSE_MASTER_DOWNLOAD_URL,
            headers=get_auth_headers(),
            json=payload.model_dump(),
        )
        response = await client.send(request, stream=True)
    except httpx.ConnectError as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Cannot connect to NSE: {e}")
    except httpx.TimeoutException:
        await client.aclose()
        raise HTTPException(status_code=504, detail="NSE request timed out")
    except Exception as e:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")

    logger.info(
        "NSE master-download response headers received in %.3fs (status=%s)",
        time.perf_counter() - started_at,
        response.status_code,
    )
    if response.status_code == 403:
        await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=403, detail="NSE authentication failed")
    if response.status_code != 200:
        try:
            body = await response.aread()
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="NSE request timed out")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")
        finally:
            await response.aclose()
            await client.aclose()
        raise HTTPException(status_code=response.status_code, detail=body.decode(errors="replace"))

    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            body = await response.aread()
            return JSONResponse(content=response.json())
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="NSE request timed out")
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"NSE request failed: {e}")
        except ValueError:
            raise HTTPException(status_code=502, detail="NSE returned invalid JSON")
        finally:
            await response.aclose()
            await client.aclose()

    media_type = content_type.split(";", 1)[0].strip() or "text/plain"

    async def stream_file():
        cached_content = bytearray() if cacheable else None
        completed = False
        try:
            async for chunk in response.aiter_bytes():
                if cached_content is not None:
                    cached_content.extend(chunk)
                yield chunk
            completed = True
        except httpx.RequestError:
            logger.exception("NSE master-download stream failed")
            raise
        finally:
            if completed and cached_content is not None:
                _master_download_cache[cache_key] = (
                    time.monotonic() + _MASTER_DOWNLOAD_CACHE_TTL_SECONDS,
                    bytes(cached_content),
                    media_type,
                )
            try:
                await response.aclose()
            finally:
                await client.aclose()
            logger.info(
                "NSE master-download stream closed after %.3fs",
                time.perf_counter() - started_at,
            )

    headers = {"X-Master-Cache": "MISS"} if cacheable else None
    return StreamingResponse(stream_file(), media_type=media_type, headers=headers)


@router.post("/client-kyc", response_model=GenericReportResponse)
async def client_kyc_report(payload: ClientKYCReportRequest, token_payload: dict = Depends(require_auth_payload)):
    auto_code = (token_payload.get("client_codes") or [None])[0]
    if not payload.client_code:
        if not auto_code:
            raise HTTPException(status_code=422, detail="client_code is required")
        payload.client_code = auto_code
    assert_token_owns_client_code(token_payload, payload.client_code)
    logger.info(f"Sending to NSE URL: {NSE_CLIENT_KYC_REPORT_URL}")
    return await _fetch_report(NSE_CLIENT_KYC_REPORT_URL, payload.model_dump(exclude_none=True))
