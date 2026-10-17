"""Azure OpenAI (Microsoft Foundry) chat model: one gpt-4o deployment for screening, SQL and answers.

Two endpoint styles are supported, chosen from AZURE_OPENAI_ENDPOINT:
  - v1 API (Foundry's current default): https://<resource>.openai.azure.com/openai/v1
      -> OpenAI client with base_url; no api-version needed; `model` = deployment name
  - classic: https://<resource>.openai.azure.com/
      -> AzureChatOpenAI with AZURE_OPENAI_API_VERSION
"""

import os

from langchain_openai import AzureChatOpenAI, ChatOpenAI

from .config import Settings


def _model(settings: Settings, deployment: str):
    if not settings.azure_endpoint or not settings.azure_api_key:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set (Microsoft Foundry).")
    kwargs = {"timeout": 60, "max_retries": 2}
    temperature = os.environ.get("AZURE_OPENAI_TEMPERATURE", "0")
    if temperature != "":                      # set it empty for reasoning models that reject temperature
        kwargs["temperature"] = float(temperature)

    endpoint = settings.azure_endpoint.rstrip("/")
    if endpoint.endswith("/openai/v1"):
        return ChatOpenAI(base_url=endpoint + "/", api_key=settings.azure_api_key, model=deployment, **kwargs)
    return AzureChatOpenAI(azure_endpoint=endpoint, api_key=settings.azure_api_key,
                           api_version=settings.azure_api_version, azure_deployment=deployment, **kwargs)


def chat_model(settings: Settings):
    return _model(settings, settings.chat_deployment)
