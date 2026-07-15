from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.services.llm_client import GitHubModelsClient


class LangChainTokenUsageCallback(BaseCallbackHandler):
    """
    Captura los tokens utilizados por las llamadas realizadas
    directamente desde LangChain.
    """

    def __init__(self, usage_recorder: GitHubModelsClient) -> None:
        # Reutilizamos el contador central del proyecto.
        self.usage_recorder = usage_recorder

    def on_llm_end(
        self,
        response: LLMResult,
        **kwargs: Any,
    ) -> None:
        """
        Se ejecuta cuando LangChain termina una llamada al modelo.

        Los mensajes TOKEN DEBUG son temporales y permiten comprobar
        que los tokens de planificación llegan al contador central.
        """

        print("[TOKEN DEBUG] Callback de LangChain ejecutado.")

        usage = self._extract_usage(response)

        if not usage:
            llm_output = getattr(response, "llm_output", None) or {}
            generations = getattr(response, "generations", None) or []

            print(
                "[TOKEN DEBUG] No se encontró información de tokens. "
                f"Claves disponibles en llm_output: {list(llm_output.keys())}. "
                f"Grupos de generaciones: {len(generations)}"
            )
            return

        # Obtenemos el consumo acumulado antes de registrar
        # la llamada realizada directamente por LangChain.
        summary_before = self.usage_recorder.get_usage_summary()

        # Agregamos los tokens al contador central del proyecto.
        self.usage_recorder.record_external_usage(usage)

        # Consultamos nuevamente el resumen para verificar el cambio.
        summary_after = self.usage_recorder.get_usage_summary()

        print(f"[TOKEN DEBUG] Tokens detectados: {usage}")
        print(
            "[TOKEN DEBUG] Total antes de planificación: "
            f"{summary_before['total_tokens']}"
        )
        print(
            "[TOKEN DEBUG] Total después de planificación: "
            f"{summary_after['total_tokens']}"
        )

    @classmethod
    def _extract_usage(
        cls,
        response: LLMResult,
    ) -> dict[str, int] | None:
        """
        Busca el consumo de tokens en las distintas ubicaciones
        donde LangChain puede incluir esta información.
        """

        # Primera ubicación: resumen general de la llamada al modelo.
        llm_output = getattr(response, "llm_output", None) or {}

        usage = (
            llm_output.get("token_usage")
            or llm_output.get("usage")
            or llm_output.get("usage_metadata")
        )

        normalized_usage = cls._normalize_usage(usage)

        if normalized_usage:
            return normalized_usage

        # Segunda ubicación: metadatos de los mensajes generados.
        generations = getattr(response, "generations", None) or []

        for generation_group in generations:
            # Algunas respuestas contienen listas de generaciones.
            # Otras pueden entregar directamente una generación.
            if isinstance(generation_group, (list, tuple)):
                generation_items = generation_group
            else:
                generation_items = [generation_group]

            for generation in generation_items:
                message = getattr(generation, "message", None)

                if message is None:
                    continue

                # LangChain suele guardar aquí input_tokens,
                # output_tokens y total_tokens.
                usage_metadata = getattr(
                    message,
                    "usage_metadata",
                    None,
                )

                normalized_usage = cls._normalize_usage(
                    usage_metadata
                )

                if normalized_usage:
                    return normalized_usage

                # Algunos modelos compatibles con OpenAI guardan
                # prompt_tokens y completion_tokens en este campo.
                response_metadata = getattr(
                    message,
                    "response_metadata",
                    None,
                ) or {}

                usage = (
                    response_metadata.get("token_usage")
                    or response_metadata.get("usage")
                    or response_metadata.get("usage_metadata")
                )

                normalized_usage = cls._normalize_usage(usage)

                if normalized_usage:
                    return normalized_usage

                # También revisamos los argumentos adicionales,
                # porque algunos proveedores almacenan allí metadatos.
                additional_kwargs = getattr(
                    message,
                    "additional_kwargs",
                    None,
                ) or {}

                usage = (
                    additional_kwargs.get("token_usage")
                    or additional_kwargs.get("usage")
                    or additional_kwargs.get("usage_metadata")
                )

                normalized_usage = cls._normalize_usage(usage)

                if normalized_usage:
                    return normalized_usage

        # Respaldo final: convertimos toda la respuesta en un
        # diccionario y buscamos recursivamente un bloque de tokens.
        try:
            if hasattr(response, "model_dump"):
                response_data = response.model_dump()
            elif hasattr(response, "dict"):
                response_data = response.dict()
            else:
                response_data = vars(response)
        except Exception:
            response_data = {}

        return cls._find_usage(response_data)

    @classmethod
    def _find_usage(
        cls,
        value: Any,
    ) -> dict[str, int] | None:
        """
        Recorre diccionarios y listas hasta encontrar un bloque
        que contenga información válida de tokens.
        """

        if isinstance(value, Mapping):
            # Primero revisamos los nombres explícitos utilizados
            # habitualmente para almacenar consumo.
            for key in (
                "usage_metadata",
                "token_usage",
                "usage",
            ):
                if key not in value:
                    continue

                normalized_usage = cls._normalize_usage(
                    value[key]
                )

                if normalized_usage:
                    return normalized_usage

            # El diccionario actual podría ser directamente
            # el bloque que contiene los valores de tokens.
            normalized_usage = cls._normalize_usage(value)

            if normalized_usage:
                return normalized_usage

            # Si no encontramos tokens, continuamos buscando
            # dentro de sus valores.
            for nested_value in value.values():
                found_usage = cls._find_usage(nested_value)

                if found_usage:
                    return found_usage

        elif isinstance(value, (list, tuple)):
            # Recorremos todos los elementos de listas o tuplas.
            for item in value:
                found_usage = cls._find_usage(item)

                if found_usage:
                    return found_usage

        return None

    @classmethod
    def _normalize_usage(
        cls,
        usage: Any,
    ) -> dict[str, int] | None:
        """
        Normaliza los nombres de tokens usados por LangChain,
        OpenAI y otros proveedores compatibles.
        """

        if not isinstance(usage, Mapping):
            return None

        # LangChain suele usar input_tokens y output_tokens.
        # La API compatible con OpenAI suele utilizar
        # prompt_tokens y completion_tokens.
        input_tokens = cls._safe_int(
            usage.get(
                "input_tokens",
                usage.get("prompt_tokens", 0),
            )
        )

        output_tokens = cls._safe_int(
            usage.get(
                "output_tokens",
                usage.get("completion_tokens", 0),
            )
        )

        total_tokens = cls._safe_int(
            usage.get("total_tokens", 0)
        )

        # Si el proveedor no informa el total, lo calculamos.
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens

        # Evitamos interpretar como consumo un diccionario
        # que no contiene realmente valores de tokens.
        if total_tokens <= 0:
            return None

        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    @staticmethod
    def _safe_int(value: Any) -> int:
        """
        Convierte un valor de tokens a entero sin interrumpir
        la ejecución si el proveedor entrega un dato inválido.
        """

        try:
            return max(int(value or 0), 0)
        except (TypeError, ValueError):
            return 0