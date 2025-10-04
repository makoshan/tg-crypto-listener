"""Translation provider implementations and helpers."""

from __future__ import annotations

import abc
import asyncio
import base64
import datetime as _dt
import hashlib
import hmac
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

try:  # pragma: no cover - optional dependencies
    import deepl
except ImportError:  # pragma: no cover - runtime fallback
    deepl = None  # type: ignore


class TranslationProviderError(RuntimeError):
    """Raised when a translation provider fails irrecoverably."""


@dataclass(slots=True)
class ProviderResult:
    """Container for provider translation responses."""

    text: str
    detected_source: str | None = None
    extra: dict[str, Any] | None = None


class BaseTranslationProvider(abc.ABC):
    """Abstract translation provider interface."""

    name: str

    def __init__(self, timeout: float = 8.0) -> None:
        self._timeout = timeout

    @abc.abstractmethod
    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        """Translate *text* into *target_language* optionally using *source_language*."""

    def supports_language(self, _: str) -> bool:  # pragma: no cover - overridable hook
        return True


class DeepLProvider(BaseTranslationProvider):
    """DeepL translation provider leveraging official SDK."""

    name = "deepl"

    def __init__(self, api_key: str, api_url: str | None, timeout: float = 8.0) -> None:
        if not api_key:
            raise TranslationProviderError("DeepL API key 未配置")
        if deepl is None:
            raise TranslationProviderError("deepl SDK 未安装，无法使用 DeepL 翻译")
        super().__init__(timeout)
        self._client = self._init_client(api_key, api_url)

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        kwargs: dict[str, str] = {"target_lang": target_language.upper()}
        if source_language:
            kwargs["source_lang"] = source_language.upper()
        try:
            response = await asyncio.to_thread(self._client.translate_text, text, **kwargs)
        except Exception as exc:  # pragma: no cover - SDK raises runtime errors
            raise TranslationProviderError(str(exc)) from exc
        translated_text = self._extract_text(response)
        if not translated_text:
            raise TranslationProviderError("DeepL 返回空结果")
        detected = getattr(response, "detected_source_lang", None)
        detected_lang = None
        if detected:
            detected_lang = str(detected).lower()
        return ProviderResult(text=translated_text, detected_source=detected_lang)

    def _init_client(self, api_key: str, api_url: str | None):
        client_kwargs: dict[str, str] = {}
        client_cls = getattr(deepl, "DeepLClient", None)
        if client_cls is None:  # pragma: no cover - legacy SDK fallback
            raise TranslationProviderError("当前 deepl SDK 版本缺少 DeepLClient 类，请升级依赖")
        normalized_url = (api_url or "").strip()
        if normalized_url:
            normalized_url = normalized_url.rstrip("/")
            if normalized_url.endswith("/v2/translate"):
                normalized_url = normalized_url[: -len("/v2/translate")]
            if normalized_url:
                client_kwargs["server_url"] = normalized_url
        try:
            return client_cls(api_key, **client_kwargs)
        except TypeError:
            client_kwargs.pop("server_url", None)
            return client_cls(api_key)
        except Exception as exc:  # pragma: no cover - SDK initialisation issues
            raise TranslationProviderError(str(exc)) from exc

    @staticmethod
    def _extract_text(result: Any) -> str:
        if isinstance(result, str):
            return result
        if isinstance(result, (list, tuple)) and result:
            return DeepLProvider._extract_text(result[0])
        return str(getattr(result, "text", ""))


class AzureTranslatorProvider(BaseTranslationProvider):
    """Microsoft Translator Text API provider."""

    name = "azure"

    def __init__(self, api_key: str, region: str, endpoint: str | None, timeout: float = 8.0) -> None:
        if not api_key:
            raise TranslationProviderError("Azure Translator key 未配置")
        if not region:
            raise TranslationProviderError("Azure Translator region 未配置")
        super().__init__(timeout)
        base_endpoint = (endpoint or "https://api.cognitive.microsofttranslator.com").rstrip("/")
        self._endpoint = f"{base_endpoint}/translate"
        self._api_key = api_key
        self._region = region

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        params: dict[str, Any] = {
            "api-version": "3.0",
            "to": [self._map_target(target_language)],
        }
        if source_language:
            params["from"] = self._map_language(source_language)
        headers = {
            "Ocp-Apim-Subscription-Key": self._api_key,
            "Ocp-Apim-Subscription-Region": self._region,
            "Content-Type": "application/json",
            "X-ClientTraceId": _random_uuid(),
        }
        payload = [{"text": text}]
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, params=params, json=payload, headers=headers)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Azure Translator 错误: {response.status_code} {response.text}")
        data = response.json()
        translations = data[0]["translations"] if data and isinstance(data, list) else []
        if not translations:
            raise TranslationProviderError("Azure Translator 返回空结果")
        translated = translations[0]
        text_out = translated.get("text")
        if not text_out:
            raise TranslationProviderError("Azure Translator 返回空文本")
        detected = data[0].get("detectedLanguage", {}).get("language") if data else None
        return ProviderResult(text=text_out, detected_source=detected)

    @staticmethod
    def _map_target(lang: str) -> str:
        if lang.lower() in {"zh", "zh-cn", "zh-hans"}:
            return "zh-Hans"
        return lang

    @staticmethod
    def _map_language(lang: str) -> str:
        if lang.lower() == "zh":
            return "zh-Hans"
        return lang


class GoogleTranslateProvider(BaseTranslationProvider):
    """Google Cloud Translation v2 provider."""

    name = "google"

    def __init__(self, api_key: str, endpoint: str | None = None, timeout: float = 8.0) -> None:
        if not api_key:
            raise TranslationProviderError("Google Translate API key 未配置")
        super().__init__(timeout)
        base_endpoint = endpoint or "https://translation.googleapis.com/language/translate/v2"
        self._endpoint = base_endpoint
        self._api_key = api_key

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        params = {"key": self._api_key}
        payload: dict[str, Any] = {"q": text, "target": target_language}
        if source_language:
            payload["source"] = source_language
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, params=params, data=payload)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Google Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        translations = data.get("data", {}).get("translations")
        if not translations:
            raise TranslationProviderError("Google Translate 返回空结果")
        translated = translations[0]
        text_out = translated.get("translatedText")
        if not text_out:
            raise TranslationProviderError("Google Translate 返回空文本")
        detected = translated.get("detectedSourceLanguage")
        return ProviderResult(text=text_out, detected_source=detected)


class AmazonTranslateProvider(BaseTranslationProvider):
    """Amazon Translate provider using SigV4 signing."""

    name = "amazon"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str,
        session_token: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        if not access_key or not secret_key:
            raise TranslationProviderError("Amazon Translate AccessKey/SecretKey 未配置")
        if not region:
            raise TranslationProviderError("Amazon Translate region 未配置")
        super().__init__(timeout)
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._session_token = session_token
        self._host = f"translate.{region}.amazonaws.com"
        self._endpoint = f"https://{self._host}/"

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        amz_target = "AWSShineFrontendService_20170701.TranslateText"
        content_type = "application/x-amz-json-1.1"
        payload = {
            "Text": text,
            "TargetLanguageCode": self._map_language(target_language),
        }
        if source_language:
            payload["SourceLanguageCode"] = self._map_language(source_language)
        if "SourceLanguageCode" not in payload:
            payload["SourceLanguageCode"] = "auto"
        body = json.dumps(payload, ensure_ascii=False)
        tstamp = _dt.datetime.utcnow()
        amz_date = tstamp.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = tstamp.strftime("%Y%m%d")
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{self._host}\n"
            f"x-amz-date:{amz_date}\n"
            f"x-amz-target:{amz_target}\n"
        )
        signed_headers = "content-type;host;x-amz-date;x-amz-target"
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                "/",
                "",
                canonical_headers,
                "",
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{self._region}/translate/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _aws_sign(self._secret_key, date_stamp, self._region, "translate")
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization_header = (
            "AWS4-HMAC-SHA256 "
            f"Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        headers = {
            "Content-Type": content_type,
            "X-Amz-Date": amz_date,
            "X-Amz-Target": amz_target,
            "Authorization": authorization_header,
        }
        if self._session_token:
            headers["X-Amz-Security-Token"] = self._session_token
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, headers=headers, content=body)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Amazon Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        translated_text = data.get("TranslatedText")
        if not translated_text:
            raise TranslationProviderError("Amazon Translate 返回空文本")
        detected = data.get("SourceLanguageCode")
        return ProviderResult(text=translated_text, detected_source=detected)

    @staticmethod
    def _map_language(lang: str) -> str:
        mapping = {"zh": "zh", "zh-cn": "zh", "zh-hans": "zh"}
        return mapping.get(lang.lower(), lang)


class BaiduTranslateProvider(BaseTranslationProvider):
    """Baidu Translate VIP API provider."""

    name = "baidu"

    def __init__(self, app_id: str, app_secret: str, timeout: float = 8.0) -> None:
        if not app_id or not app_secret:
            raise TranslationProviderError("Baidu Translate appid/secret 未配置")
        super().__init__(timeout)
        self._app_id = app_id
        self._app_secret = app_secret
        self._endpoint = "https://fanyi-api.baidu.com/api/trans/vip/translate"

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        salt = str(random.randint(32768, 65536))
        src = source_language or "auto"
        tgt = self._map_language(target_language)
        sign_raw = f"{self._app_id}{text}{salt}{self._app_secret}"
        sign = hashlib.md5(sign_raw.encode("utf-8")).hexdigest()
        params = {
            "q": text,
            "from": src,
            "to": tgt,
            "appid": self._app_id,
            "salt": salt,
            "sign": sign,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, data=params)
        data = response.json()
        if "error_code" in data:
            raise TranslationProviderError(f"Baidu Translate 错误: {data.get('error_msg', data['error_code'])}")
        result = data.get("trans_result")
        if not result:
            raise TranslationProviderError("Baidu Translate 返回空结果")
        dst = result[0].get("dst")
        if not dst:
            raise TranslationProviderError("Baidu Translate 返回空文本")
        detected = result[0].get("src")
        return ProviderResult(text=dst, detected_source=detected)

    @staticmethod
    def _map_language(lang: str) -> str:
        return "zh" if lang.lower().startswith("zh") else lang


class NiuTransProvider(BaseTranslationProvider):
    """NiuTrans open API provider."""

    name = "niutrans"

    def __init__(self, api_key: str, endpoint: str | None = None, timeout: float = 8.0) -> None:
        if not api_key:
            raise TranslationProviderError("NiuTrans API key 未配置")
        super().__init__(timeout)
        self._api_key = api_key
        self._endpoint = (endpoint or "https://api.niutrans.com/NiuTransServer/translation").rstrip("/")

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        params = {
            "apikey": self._api_key,
            "from": source_language or "auto",
            "to": target_language,
        }
        data = {"src_text": text}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, params=params, data=data)
        if response.status_code >= 400:
            raise TranslationProviderError(f"NiuTrans 错误: {response.status_code} {response.text}")
        result = response.json()
        if "error_code" in result:
            raise TranslationProviderError(f"NiuTrans 错误: {result.get('error_msg', result['error_code'])}")
        target_text = result.get("tgt_text")
        if not target_text:
            raise TranslationProviderError("NiuTrans 返回空文本")
        detected = result.get("from")
        return ProviderResult(text=target_text, detected_source=detected)


class VolcanoTranslateProvider(BaseTranslationProvider):
    """Volcano Engine translation provider using Volcengine signature v4."""

    name = "volcano"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        region: str = "cn-north-1",
        endpoint: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        if not access_key or not secret_key:
            raise TranslationProviderError("Volcano Engine AccessKey/SecretKey 未配置")
        super().__init__(timeout)
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        host = endpoint or "translate.volcengineapi.com"
        self._host = host
        self._endpoint = f"https://{host}/"

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        action = "TranslateText"
        version = "2020-06-01"
        service = "translate"
        request_body = json.dumps(
            {
                "SourceLanguage": source_language or "auto",
                "TargetLanguage": target_language,
                "TextList": [text],
            },
            ensure_ascii=False,
        )
        date = _dt.datetime.utcnow()
        x_date = date.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = date.strftime("%Y%m%d")
        canonical_headers = (
            f"content-type:application/json\n"
            f"host:{self._host}\n"
            f"x-date:{x_date}\n"
        )
        signed_headers = "content-type;host;x-date"
        canonical_query = f"Action={action}&Version={version}"
        payload_hash = hashlib.sha256(request_body.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                "/",
                canonical_query,
                canonical_headers,
                "",
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{self._region}/{service}/request"
        string_to_sign = "\n".join(
            [
                "HMAC-SHA256",
                x_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _volc_sign(self._secret_key, date_stamp, self._region, service)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "HMAC-SHA256 "
            f"Credential={self._access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )
        headers = {
            "Content-Type": "application/json",
            "Host": self._host,
            "X-Date": x_date,
            "Authorization": authorization,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._endpoint,
                params={"Action": action, "Version": version},
                headers=headers,
                content=request_body,
            )
        if response.status_code >= 400:
            raise TranslationProviderError(f"Volcano Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        translations = data.get("TranslationList") or data.get("Data", {}).get("TranslationList") or []
        if not translations:
            raise TranslationProviderError("Volcano Translate 返回空结果")
        first = translations[0]
        target_text = first.get("Translation") or first.get("TranslatedText")
        if not target_text:
            raise TranslationProviderError("Volcano Translate 返回空文本")
        detected = first.get("DetectedSourceLanguage")
        return ProviderResult(text=target_text, detected_source=detected)


class TencentTranslateProvider(BaseTranslationProvider):
    """Tencent Cloud TMT provider using TC3 signature."""

    name = "tencent"

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        region: str = "ap-beijing",
        project_id: int | None = None,
        timeout: float = 8.0,
    ) -> None:
        if not secret_id or not secret_key:
            raise TranslationProviderError("Tencent Translate SecretId/SecretKey 未配置")
        super().__init__(timeout)
        self._secret_id = secret_id
        self._secret_key = secret_key
        self._region = region
        self._project_id = project_id
        self._service = "tmt"
        self._host = "tmt.tencentcloudapi.com"
        self._endpoint = f"https://{self._host}"

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        action = "TextTranslate"
        version = "2018-03-21"
        timestamp = int(time.time())
        headers = {"Content-Type": "application/json"}
        payload: dict[str, Any] = {
            "SourceText": text,
            "Source": (source_language or "auto").replace("-", ""),
            "Target": target_language.replace("-", ""),
            "ProjectId": self._project_id or 0,
        }
        body = json.dumps(payload, ensure_ascii=False)
        canonical_request = _tc3_canonical_request(self._host, body)
        credential_scope, string_to_sign = _tc3_string_to_sign(
            body,
            timestamp,
            self._service,
            canonical_request,
        )
        signature = _tc3_signature(self._secret_key, credential_scope, string_to_sign)
        headers.update(
            {
                "Authorization": (
                    "TC3-HMAC-SHA256 "
                    f"Credential={self._secret_id}/{credential_scope}, "
                    "SignedHeaders=content-type;host, "
                    f"Signature={signature}"
                ),
                "Host": self._host,
                "X-TC-Action": action,
                "X-TC-Timestamp": str(timestamp),
                "X-TC-Version": version,
                "X-TC-Region": self._region,
            }
        )
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, headers=headers, content=body)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Tencent Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        if "Error" in (data.get("Response") or {}):
            err = data["Response"]["Error"]
            raise TranslationProviderError(f"Tencent Translate 错误: {err.get('Message', err.get('Code'))}")
        translated_text = data.get("Response", {}).get("TargetText")
        if not translated_text:
            raise TranslationProviderError("Tencent Translate 返回空文本")
        detected = data.get("Response", {}).get("Source")
        return ProviderResult(text=translated_text, detected_source=detected)


class AlibabaTranslateProvider(BaseTranslationProvider):
    """Alibaba Cloud Machine Translation RPC API provider."""

    name = "alibaba"

    def __init__(
        self,
        access_key_id: str,
        access_key_secret: str,
        app_key: str,
        region_id: str = "cn-hangzhou",
        timeout: float = 8.0,
    ) -> None:
        if not access_key_id or not access_key_secret:
            raise TranslationProviderError("Alibaba Cloud AccessKeyId/Secret 未配置")
        if not app_key:
            raise TranslationProviderError("Alibaba Cloud AppKey 未配置")
        super().__init__(timeout)
        self._access_key_id = access_key_id
        self._access_key_secret = access_key_secret
        self._app_key = app_key
        self._region_id = region_id
        self._endpoint = "https://mt.cn-hangzhou.aliyuncs.com/"

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        sys_params = {
            "Format": "JSON",
            "Version": "2018-10-12",
            "AccessKeyId": self._access_key_id,
            "SignatureMethod": "HMAC-SHA1",
            "Timestamp": _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "SignatureVersion": "1.0",
            "SignatureNonce": _random_uuid(),
            "Action": "TranslateGeneral",
            "RegionId": self._region_id,
        }
        biz_params = {
            "FormatType": "text",
            "Scene": "general",
            "SourceLanguage": (source_language or "auto").replace("_", "-"),
            "TargetLanguage": target_language.replace("_", "-"),
            "ApiType": "com",
            "Text": text,
            "AppKey": self._app_key,
        }
        all_params: dict[str, str] = {**sys_params, **biz_params}
        sorted_items = sorted((k, v) for k, v in all_params.items())
        canonicalized = "&".join(
            f"{_percent_encode(k)}={_percent_encode(v)}" for k, v in sorted_items
        )
        string_to_sign = f"POST&%2F&{_percent_encode(canonicalized)}"
        key = f"{self._access_key_secret}&"
        signature = base64.b64encode(
            hmac.new(key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
        ).decode("utf-8")
        all_params["Signature"] = signature
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, data=all_params)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Alibaba Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        if data.get("Code") != "200" or "Data" not in data:
            message = data.get("Message", data.get("Code", "Unknown error"))
            raise TranslationProviderError(f"Alibaba Translate 错误: {message}")
        result = data["Data"].get("Translated")
        if not result:
            raise TranslationProviderError("Alibaba Translate 返回空文本")
        detected = data["Data"].get("DetectedLanguage")
        return ProviderResult(text=result, detected_source=detected)


class HuaweiTranslateProvider(BaseTranslationProvider):
    """Huawei Cloud Machine Translation provider using AK/SK signing."""

    name = "huawei"

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        project_id: str,
        region: str = "cn-north-4",
        endpoint: str | None = None,
        timeout: float = 8.0,
    ) -> None:
        if not access_key or not secret_key:
            raise TranslationProviderError("Huawei Cloud AccessKey/Secret 未配置")
        if not project_id:
            raise TranslationProviderError("Huawei Cloud ProjectId 未配置")
        super().__init__(timeout)
        host = endpoint or f"translate.{region}.myhuaweicloud.com"
        self._host = host
        self._path = f"/v1/{project_id}/machine-translation/text-translation"
        self._endpoint = f"https://{host}{self._path}"
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region

    async def translate(
        self,
        text: str,
        source_language: str | None,
        target_language: str,
    ) -> ProviderResult:
        body = json.dumps(
            {
                "from": source_language or "auto",
                "to": target_language,
                "text": text,
            },
            ensure_ascii=False,
        )
        sdk_date = _dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical_request = (
            "POST\n"
            f"{self._path}\n"
            "\n"
            "content-type:application/json\n"
            f"host:{self._host}\n"
            f"x-sdk-date:{sdk_date}\n"
            "\n"
            "content-type;host;x-sdk-date\n"
            f"{payload_hash}"
        )
        string_to_sign = (
            "SDK-HMAC-SHA256\n"
            f"{sdk_date}\n"
            f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
        )
        signature = hmac.new(
            self._secret_key.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        authorization = (
            "SDK-HMAC-SHA256 "
            f"Access={self._access_key}, SignedHeaders=content-type;host;x-sdk-date, Signature={signature}"
        )
        headers = {
            "Content-Type": "application/json",
            "Host": self._host,
            "X-Sdk-Date": sdk_date,
            "Authorization": authorization,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(self._endpoint, headers=headers, content=body)
        if response.status_code >= 400:
            raise TranslationProviderError(f"Huawei Translate 错误: {response.status_code} {response.text}")
        data = response.json()
        if data.get("error_code"):
            raise TranslationProviderError(f"Huawei Translate 错误: {data.get('error_msg', data['error_code'])}")
        translated_text = data.get("translated_text") or data.get("translate_text")
        if not translated_text:
            raise TranslationProviderError("Huawei Translate 返回空文本")
        detected = data.get("from")
        return ProviderResult(text=translated_text, detected_source=detected)


ProviderFactory = Mapping[str, type[BaseTranslationProvider]]


def build_provider(
    name: str,
    timeout: float,
    credentials: Mapping[str, Any],
) -> BaseTranslationProvider:
    """Instantiate a provider by name with *credentials*."""

    normalized = name.strip().lower()
    try:
        builder = _PROVIDER_BUILDERS[normalized]
    except KeyError as exc:  # pragma: no cover - configuration issue
        raise TranslationProviderError(f"未知翻译提供商: {name}") from exc
    return builder(timeout=timeout, **credentials)


def available_providers() -> Sequence[str]:  # pragma: no cover - helper
    return list(_PROVIDER_BUILDERS.keys())


def _random_uuid() -> str:
    import uuid

    return str(uuid.uuid4())


def _aws_sign(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key_date = hmac.new(("AWS4" + secret_key).encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    key_region = hmac.new(key_date, region.encode("utf-8"), hashlib.sha256).digest()
    key_service = hmac.new(key_region, service.encode("utf-8"), hashlib.sha256).digest()
    key_signing = hmac.new(key_service, b"aws4_request", hashlib.sha256).digest()
    return key_signing


def _tc3_canonical_request(host: str, body: str) -> str:
    payload_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    canonical_request = (
        "POST\n"
        "/\n"
        "\n"
        f"content-type:application/json\n"
        f"host:{host}\n"
        "\n"
        "content-type;host\n"
        f"{payload_hash}"
    )
    return canonical_request


def _tc3_string_to_sign(
    body: str,
    timestamp: int,
    service: str,
    canonical_request: str,
) -> tuple[str, str]:
    date = _dt.datetime.utcfromtimestamp(timestamp).strftime("%Y-%m-%d")
    credential_scope = f"{date}/{service}/tc3_request"
    hashed_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    string_to_sign = (
        "TC3-HMAC-SHA256\n"
        f"{timestamp}\n"
        f"{credential_scope}\n"
        f"{hashed_request}"
    )
    return credential_scope, string_to_sign


def _tc3_signature(secret_key: str, credential_scope: str, string_to_sign: str) -> str:
    date, service, _ = credential_scope.split("/")
    secret_date = hmac.new(("TC3" + secret_key).encode("utf-8"), date.encode("utf-8"), hashlib.sha256).digest()
    secret_service = hmac.new(secret_date, service.encode("utf-8"), hashlib.sha256).digest()
    secret_signing = hmac.new(secret_service, b"tc3_request", hashlib.sha256).digest()
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return signature


def _volc_sign(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    k_date = hmac.new(secret_key.encode("utf-8"), date_stamp.encode("utf-8"), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode("utf-8"), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode("utf-8"), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    return k_signing


def _percent_encode(value: str) -> str:
    safe_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    res = []
    for ch in value:
        if ch in safe_chars:
            res.append(ch)
        else:
            res.append("%" + format(ord(ch), "02X"))
    return "".join(res)


def _builder_from_cls(cls: type[BaseTranslationProvider]):
    def _builder(*, timeout: float, **kwargs: Any) -> BaseTranslationProvider:
        return cls(timeout=timeout, **kwargs)

    return _builder


_PROVIDER_BUILDERS: dict[str, Any] = {
    "deepl": _builder_from_cls(DeepLProvider),
    "azure": _builder_from_cls(AzureTranslatorProvider),
    "google": _builder_from_cls(GoogleTranslateProvider),
    "amazon": _builder_from_cls(AmazonTranslateProvider),
    "baidu": _builder_from_cls(BaiduTranslateProvider),
    "niutrans": _builder_from_cls(NiuTransProvider),
    "volcano": _builder_from_cls(VolcanoTranslateProvider),
    "tencent": _builder_from_cls(TencentTranslateProvider),
    "alibaba": _builder_from_cls(AlibabaTranslateProvider),
    "huawei": _builder_from_cls(HuaweiTranslateProvider),
}
