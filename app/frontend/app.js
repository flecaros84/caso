// Estado global simple del frontend.
// Centralizamos aquí los datos que se cargan desde el backend y el estado
// de la ejecución actual, para evitar variables sueltas en distintas partes.
const state = {
  announcements: [],
  cvs: [],
  llmStatus: null,
  models: [],
  defaultModel: null,
  currentJobId: null,
  progressTimer: null,
  useAgentFlow: false
};

// Helper corto para obtener elementos del DOM por id.
// Ejemplo: $("status") equivale a document.getElementById("status").
const $ = (id) => document.getElementById(id);

/**
 * Wrapper genérico para llamar a la API.
 *
 * - Agrega Content-Type JSON por defecto.
 * - Si la respuesta falla, intenta leer el detalle entregado por FastAPI.
 * - Si no hay detalle, usa el statusText del navegador.
 */
async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || "Error inesperado");
  }

  return response.json();
}

/**
 * Carga el estado actual del LLM desde el backend.
 *
 * Esto permite mostrar si el modelo está configurado, si existe token,
 * qué proveedor se usará y si el análisis correrá con LLM o fallback local.
 */
async function loadLLMStatus() {
  const data = await api("/api/llm/status");
  state.llmStatus = data;
  renderLLMStatus(data);
}

/**
 * Carga el catálogo de modelos disponibles.
 *
 * El backend entrega una lista de modelos y un modelo por defecto.
 * Luego se actualiza el selector del frontend.
 */
async function loadModelCatalog() {
  const data = await api("/api/llm/models");
  state.models = data.models || [];
  state.defaultModel = data.default_model || null;
  renderModelSelector();
}

/**
 * Renderiza el selector de modelos.
 *
 * Si el catálogo no existe, se usa como fallback el modelo configurado
 * en el estado del LLM o el modelo por defecto esperado.
 */
function renderModelSelector() {
  const select = $("modelSelect");
  const description = $("modelDescription");

  if (!select) return;

  select.innerHTML = "";

  const models = state.models || [];

  if (!models.length) {
    const option = document.createElement("option");
    option.value = state.llmStatus?.model || "openai/gpt-4o-mini";
    option.textContent = state.llmStatus?.model || "openai/gpt-4o-mini";
    select.appendChild(option);

    if (description) {
      description.textContent = "No se encontró catálogo local. Se usará el modelo configurado por defecto.";
    }

    return;
  }

  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = `${model.name || model.id} — ${model.id}`;

    // Marcamos como seleccionado el modelo por defecto.
    if (model.id === state.defaultModel || model.default) {
      option.selected = true;
    }

    select.appendChild(option);
  }

  updateModelDescription();
}

/**
 * Obtiene el modelo actualmente seleccionado.
 *
 * Orden de prioridad:
 * 1. Valor seleccionado en el dropdown.
 * 2. Modelo por defecto del catálogo.
 * 3. Modelo informado por el estado LLM.
 * 4. null si no hay nada disponible.
 */
function getSelectedModel() {
  const select = $("modelSelect");
  return select?.value || state.defaultModel || state.llmStatus?.model || null;
}

/**
 * Define qué endpoints usar según el flujo seleccionado.
 *
 * El sistema soporta dos modos:
 * - flujo clásico;
 * - flujo con agente LangChain.
 */
function getAnalysisEndpoints() {
  const useAgentFlow = Boolean($("useAgentFlow")?.checked);

  if (useAgentFlow) {
    return {
      start: "/api/agent/analyze/start",
      status: (jobId) => `/api/agent/analyze/status/${jobId}`,
      result: (jobId) => `/api/agent/analyze/result/${jobId}`,
      label: "agente LangChain"
    };
  }

  return {
    start: "/api/analyze/start",
    status: (jobId) => `/api/analyze/status/${jobId}`,
    result: (jobId) => `/api/analyze/result/${jobId}`,
    label: "flujo clásico"
  };
}

/**
 * Actualiza la descripción del modelo seleccionado.
 *
 * Se muestra proveedor y descripción para que el usuario sepa
 * qué modelo está usando antes de ejecutar el análisis.
 */
function updateModelDescription() {
  const description = $("modelDescription");

  if (!description) return;

  const selected = getSelectedModel();
  const model = (state.models || []).find(item => item.id === selected);

  if (!model) {
    description.textContent = selected
      ? `Se usará el modelo: ${selected}`
      : "No hay modelo seleccionado.";
    return;
  }

  description.innerHTML = `
    <strong>${escapeHtml(model.provider || "GitHub Models")}</strong>:
    ${escapeHtml(model.description || "Modelo disponible para análisis.")}
  `;
}

/**
 * Renderiza el estado del LLM.
 *
 * Esta sección informa si el sistema está en modo online,
 * modo local o si falta configuración del token.
 */
function renderLLMStatus(data) {
  const container = $("llmStatus");

  if (!data) {
    container.className = "llm-status warning";
    container.textContent = "No se pudo verificar el estado del modelo.";
    return;
  }

  const isEnabled = Boolean(data.use_llm);
  const isConfigured = Boolean(data.configured);

  let badgeClass = "local";
  let badgeText = "Modo local";
  let detail = "El análisis usará reglas locales, RAG y fallback heurístico.";

  if (isEnabled) {
    badgeClass = "online";
    badgeText = "GitHub Models activo";
    detail = "El análisis intentará usar el modelo en línea para deducir competencias y evaluar evidencias.";
  } else if (!isConfigured) {
    badgeClass = "warning";
    badgeText = "Token no configurado";
    detail = "USE_LLM está desactivado o falta GITHUB_TOKEN. El sistema funcionará en modo local.";
  }

  container.className = `llm-status ${badgeClass}`;
  container.innerHTML = `
    <div class="llm-status-main">
      <span class="llm-badge ${badgeClass}">${badgeText}</span>
      <strong>${escapeHtml(data.provider || "Proveedor IA")}</strong>
    </div>
    <div class="llm-status-grid">
      <p><strong>Modelo:</strong> ${escapeHtml(data.model || "No definido")}</p>
      <p><strong>Modo:</strong> ${escapeHtml(data.mode || "local_fallback")}</p>
      <p><strong>Endpoint:</strong> ${escapeHtml(data.endpoint || "No definido")}</p>
      <p><strong>Token:</strong> ${isConfigured ? "Configurado" : "No configurado"}</p>
    </div>
    <p class="hint">${escapeHtml(detail)}</p>
  `;
}

/**
 * Carga anuncios y CV disponibles desde el backend.
 *
 * Estos archivos vienen desde las carpetas de recursos del proyecto.
 */
async function loadFiles() {
  setStatus("Cargando archivos disponibles...");

  const data = await api("/api/files");

  state.announcements = data.announcements || [];
  state.cvs = data.cvs || [];

  renderFileSelectors();
  setStatus("Archivos cargados.");
}

/**
 * Renderiza los selectores de archivos.
 *
 * - El anuncio se muestra como dropdown.
 * - Los CV se muestran como lista de checkboxes.
 */
function renderFileSelectors() {
  const announcementSelect = $("announcementSelect");
  announcementSelect.innerHTML = "";

  for (const announcement of state.announcements) {
    const option = document.createElement("option");
    option.value = announcement.id;
    option.textContent = announcement.filename;
    announcementSelect.appendChild(option);
  }

  const cvList = $("cvList");
  cvList.innerHTML = "";

  for (const cv of state.cvs) {
    const label = document.createElement("label");
    label.className = "checkbox-item";

    // El input queda marcado por defecto para facilitar la demo.
    label.innerHTML = `
      <input type="checkbox" value="${cv.id}" checked />
      <span>${escapeHtml(cv.filename)}</span>
    `;

    cvList.appendChild(label);
  }
}

/**
 * Solicita al backend extraer texto del anuncio seleccionado.
 *
 * Si el backend no puede extraer texto, se permite pegarlo manualmente.
 */
async function extractAnnouncementText() {
  const announcementId = $("announcementSelect").value;

  if (!announcementId) return;

  setStatus("Extrayendo texto del anuncio...");

  const data = await api(`/api/extract/announcement/${announcementId}`);
  $("announcementText").value = data.text || "";

  if (!data.text) {
    setStatus("No se pudo extraer texto automático. Pega el texto del anuncio manualmente.");
  } else {
    setStatus("Texto del anuncio extraído.");
  }
}

/**
 * Inicia un análisis de candidatos.
 *
 * Valida:
 * - que exista anuncio seleccionado;
 * - que exista al menos un CV seleccionado;
 * - que se obtenga el modelo seleccionado.
 *
 * Luego envía el job al backend y comienza el polling de progreso.
 */
async function analyze() {
  const announcementId = $("announcementSelect").value;
  const cvIds = [...document.querySelectorAll("#cvList input:checked")].map(input => input.value);
  const announcementText = $("announcementText").value.trim();
  const ternaSize = Number($("ternaSize").value || 3);
  const selectedModel = getSelectedModel();

  if (!announcementId) {
    setStatus("Debes seleccionar un anuncio.");
    return;
  }

  if (cvIds.length === 0) {
    setStatus("Debes seleccionar al menos un CV.");
    return;
  }

  const endpoints = getAnalysisEndpoints();

  clearPreviousResults();
  showProgressPanel();

  updateProgressUI({
    status: "queued",
    progress: 0,
    current_step: `Enviando análisis al backend usando ${endpoints.label}...`,
    llm_success: 0,
    llm_fallback: 0,
    llm_errors: 0,
    events: [],
    event_count: 0
  });

  setStatus(`Análisis iniciado usando ${endpoints.label}. Puedes seguir el avance en la barra de progreso.`);
  $("analyzeBtn").disabled = true;

  try {
    const startResponse = await api(endpoints.start, {
      method: "POST",
      body: JSON.stringify({
        announcement_id: announcementId,
        cv_ids: cvIds,
        announcement_text_override: announcementText || null,
        terna_size: ternaSize,
        selected_model: selectedModel
      })
    });

    state.currentJobId = startResponse.job_id;
    await pollAnalysisProgress(state.currentJobId, endpoints);
  } finally {
    // Se reactivar aunque el análisis falle.
    $("analyzeBtn").disabled = false;
  }
}

/**
 * Consulta periódicamente el estado del análisis.
 *
 * El backend trabaja en segundo plano. Por eso el frontend hace polling:
 * - consulta estado;
 * - actualiza barra de progreso;
 * - si termina, busca resultado final;
 * - si falla, muestra error.
 */
async function pollAnalysisProgress(jobId, endpoints) {
  return new Promise((resolve, reject) => {
    if (state.progressTimer) {
      clearInterval(state.progressTimer);
    }

    const tick = async () => {
      try {
        const status = await api(endpoints.status(jobId));
        updateProgressUI(status);

        if (status.status === "completed") {
          clearInterval(state.progressTimer);
          state.progressTimer = null;

          const result = await api(endpoints.result(jobId));

          renderCompetencies(result.competencies || []);
          renderTerna(result.recommended_terna || result.terna || []);
          renderRanking(result.ranking || []);
          renderLLMUsage(result.llm_usage || null);
          renderReport(result.report || null);
          renderObservability(result.observability || null);
          renderAgentTrace(result.agent_trace || null);

          if (result.llm_status) {
            renderLLMStatus(result.llm_status);
          }

          setStatus(`Análisis completado usando ${endpoints.label}.`);
          resolve(result);
          return;
        }

        if (status.status === "failed") {
          clearInterval(state.progressTimer);
          state.progressTimer = null;

          setStatus(status.error || "El análisis falló.");
          updateProgressUI(status);

          reject(new Error(status.error || "El análisis falló."));
        }
      } catch (error) {
        clearInterval(state.progressTimer);
        state.progressTimer = null;

        setStatus(error.message);
        reject(error);
      }
    };

    // Ejecutamos una primera consulta inmediata y luego repetimos cada 1,5 segundos.
    tick();
    state.progressTimer = setInterval(tick, 1500);
  });
}

/**
 * Muestra el panel de progreso.
 */
function showProgressPanel() {
  $("progressPanel").classList.remove("hidden");
}

/**
 * Actualiza barra de progreso, contadores LLM y bitácora.
 *
 * Esta sección es útil para observabilidad en vivo:
 * - muestra estado actual;
 * - contabiliza LLM OK, fallback y errores;
 * - lista eventos de ejecución.
 */
function updateProgressUI(status) {
  const progress = Math.max(0, Math.min(100, Number(status.progress || 0)));

  $("progressFill").style.width = `${progress}%`;
  $("progressPercent").textContent = `${progress}%`;
  $("progressStep").textContent = status.current_step || "Procesando...";

  $("llmSuccessCount").textContent = `LLM OK: ${status.llm_success || 0}`;
  $("llmFallbackCount").textContent = `Fallback: ${status.llm_fallback || 0}`;
  $("llmErrorCount").textContent = `Errores: ${status.llm_errors || 0}`;

  const panel = $("progressPanel");

  panel.classList.remove("running", "completed", "failed");
  panel.classList.add(
    status.status === "completed"
      ? "completed"
      : status.status === "failed"
        ? "failed"
        : "running"
  );

  const events = status.events || [];
  const eventCount = Number(status.event_count || events.length || 0);
  const eventsHeader = $("progressEventsHeader");

  if (eventsHeader) {
    eventsHeader.textContent = `Bitácora completa de ejecución (${eventCount} eventos)`;
  }

  $("progressEvents").innerHTML = events.map((event, index) => `
    <div class="progress-event ${escapeHtml(event.kind || "info")}">
      <span>${String(index + 1).padStart(2, "0")}</span>
      <span>${escapeHtml(event.time || "")}</span>
      <p>${escapeHtml(event.message || "")}</p>
    </div>
  `).join("");

  const eventsContainer = $("progressEvents");

  // Mientras el análisis corre, mantenemos el scroll al final para ver el evento más reciente.
  if (status.status !== "completed") {
    eventsContainer.scrollTop = eventsContainer.scrollHeight;
  }
}

/**
 * Limpia resultados anteriores antes de iniciar un nuevo análisis.
 *
 * Esto evita mezclar resultados viejos con la ejecución nueva.
 */
function clearPreviousResults() {
  $("competencies").className = "table-container empty";
  $("competencies").textContent = "Análisis en curso...";

  $("terna").className = "ranking-grid empty";
  $("terna").textContent = "Análisis en curso...";

  $("ranking").className = "ranking-list empty";
  $("ranking").textContent = "Análisis en curso...";

  const llmUsage = $("llmUsage");

  if (llmUsage) {
    llmUsage.className = "observability-dashboard empty";
    llmUsage.textContent = "El consumo aparecerá al finalizar el análisis...";
  }

  $("report").className = "report-box empty";
  $("report").textContent = "El reporte se generará al finalizar el análisis...";

  const agentTrace = $("agentTrace");

  if (agentTrace) {
    agentTrace.className = "agent-trace empty";
    agentTrace.textContent = "La trazabilidad del agente aparecerá aquí si ejecutas el flujo LangChain.";
  }

  const observability = $("observability");

  if (observability) {
    observability.className = "observability-dashboard empty";
    observability.textContent = "Las métricas de observabilidad aparecerán al finalizar el análisis...";
  }
}

/**
 * Muestra los tokens utilizados y el costo estimado del análisis.
 *
 * Los cálculos se realizan en el backend. El frontend solamente
 * presenta los valores recibidos en llm_usage.
 */
function renderLLMUsage(usage) {
  const container = $("llmUsage");

  if (!container) return;

  if (!usage) {
    container.className = "observability-dashboard empty";
    container.textContent = "No se registraron datos de consumo del modelo.";
    return;
  }

  const promptTokens = Number(usage.prompt_tokens || 0);
  const completionTokens = Number(usage.completion_tokens || 0);
  const totalTokens = Number(usage.total_tokens || 0);

  const inputCost = Number(usage.estimated_input_cost_usd || 0);
  const outputCost = Number(usage.estimated_output_cost_usd || 0);
  const totalCost = Number(usage.estimated_total_cost_usd || 0);

  const inputPrice = Number(
    usage.input_cost_per_1m_tokens_usd || 0
  );

  const outputPrice = Number(
    usage.output_cost_per_1m_tokens_usd || 0
  );

  container.className = "observability-dashboard";

  container.innerHTML = `
    <div class="metrics-grid">
      <div class="metric-card">
        <span class="metric-label">Tokens de entrada</span>
        <strong>${promptTokens.toLocaleString("es-CL")}</strong>
        <small>Costo estimado: USD ${inputCost.toFixed(8)}</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Tokens de salida</span>
        <strong>${completionTokens.toLocaleString("es-CL")}</strong>
        <small>Costo estimado: USD ${outputCost.toFixed(8)}</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Tokens totales</span>
        <strong>${totalTokens.toLocaleString("es-CL")}</strong>
        <small>Suma de entrada y salida</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Costo total estimado</span>
        <strong>USD ${totalCost.toFixed(8)}</strong>
        <small>No corresponde necesariamente a un cobro real</small>
      </div>
    </div>

    <div class="observability-footer">
      <strong>Modelo:</strong>
      ${escapeHtml(usage.model || "No informado")}
      ·
      <strong>Tarifa de entrada:</strong>
      USD ${inputPrice} por 1M tokens
      ·
      <strong>Tarifa de salida:</strong>
      USD ${outputPrice} por 1M tokens
    </div>
  `;
}

/**
 * Renderiza los enlaces al reporte generado.
 *
 * El backend genera un Markdown y un JSON. El frontend solo muestra
 * enlaces para abrirlos.
 */
function renderReport(report) {
  const container = $("report");

  if (!report) {
    container.className = "report-box empty";
    container.textContent = "No se generó reporte local.";
    return;
  }

  const markdownUrl = report.markdown_url || "#";
  const jsonUrl = report.json_url || "#";

  container.className = "report-box";
  container.innerHTML = `
    <div class="report-summary">
      <div>
        <strong>Reporte guardado correctamente</strong>
        <p>Carpeta local: <code>${escapeHtml(report.directory || "outputs/reports")}</code></p>
        <p>Fecha: ${escapeHtml(report.created_at || "No informada")}</p>
      </div>
      <div class="report-actions">
        <a class="report-link" href="${escapeHtml(markdownUrl)}" target="_blank" rel="noopener">Abrir Markdown</a>
        <a class="report-link secondary" href="${escapeHtml(jsonUrl)}" target="_blank" rel="noopener">Abrir JSON</a>
      </div>
    </div>
    <div class="report-files">
      <p><strong>Markdown:</strong> ${escapeHtml(report.markdown_filename || "")}</p>
      <p><strong>JSON:</strong> ${escapeHtml(report.json_filename || "")}</p>
    </div>
  `;
}

/**
 * Renderiza el dashboard de observabilidad.
 *
 * Esta sección muestra métricas clave de la ejecución:
 * - latencia;
 * - cantidad de evaluaciones;
 * - éxito LLM;
 * - fallback;
 * - errores;
 * - evidencia;
 * - anomalías;
 * - recomendaciones;
 * - uso responsable.
 */
function renderObservability(observability) {
  const container = $("observability");

  if (!container) return;

  if (!observability) {
    container.className = "observability-dashboard empty";
    container.textContent = "No se generaron métricas de observabilidad.";
    return;
  }

  const dataset = observability.dataset || {};
  const llm = observability.llm || {};
  const performance = observability.performance || {};
  const ranking = observability.ranking || {};
  const quality = observability.quality || {};
  const anomalies = observability.anomalies || [];
  const recommendations = observability.recommendations || [];
  const responsibleAi = observability.responsible_ai || {};

  container.className = "observability-dashboard";

  container.innerHTML = `
    <div class="metrics-grid">
      <div class="metric-card">
        <span class="metric-label">Latencia total</span>
        <strong>${escapeHtml(performance.total_latency_seconds ?? observability.duration_seconds ?? 0)} s</strong>
        <small>Tiempo completo de ejecución</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Latencia por candidato</span>
        <strong>${escapeHtml(performance.average_latency_per_candidate_seconds ?? 0)} s</strong>
        <small>Promedio según CV evaluados</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Evaluaciones</span>
        <strong>${escapeHtml(dataset.evaluation_count ?? 0)}</strong>
        <small>
          ${escapeHtml(dataset.candidate_count ?? 0)} candidatos ·
          ${escapeHtml(dataset.competency_count ?? 0)} competencias
        </small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Éxito LLM</span>
        <strong>${escapeHtml(llm.success_rate ?? 0)}%</strong>
        <small>${escapeHtml(llm.success_count ?? 0)} llamadas correctas</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Fallback local</span>
        <strong>${escapeHtml(llm.fallback_rate ?? 0)}%</strong>
        <small>${escapeHtml(llm.fallback_count ?? 0)} usos de respaldo</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Errores</span>
        <strong>${escapeHtml(llm.error_rate ?? 0)}%</strong>
        <small>${escapeHtml(llm.error_count ?? 0)} eventos con error</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Evidencia promedio</span>
        <strong>${escapeHtml(quality.average_evidence_score ?? 0)}/4</strong>
        <small>${escapeHtml(quality.strong_evidence_rate ?? 0)}% evidencia fuerte o clara</small>
      </div>

      <div class="metric-card">
        <span class="metric-label">Puntaje promedio</span>
        <strong>${escapeHtml(ranking.average_score ?? 0)}/100</strong>
        <small>
          ${
            (dataset.candidate_count ?? 0) > 1
              ? `Margen top 1 vs top 2: ${escapeHtml(ranking.top_candidate_margin ?? 0)}`
              : "Margen top 1 vs top 2: no aplica"
          }
        </small>
      </div>
    </div>

    <div class="observability-columns">
      <div class="observability-panel">
        <h3>Anomalías detectadas</h3>
        ${
          anomalies.length
            ? `<ul>${anomalies.map(item => `
                <li>
                  <strong>${escapeHtml(item.severity || "sin severidad")}:</strong>
                  ${escapeHtml(item.message || "")}
                </li>
              `).join("")}</ul>`
            : `<p>No se detectaron anomalías críticas en esta ejecución.</p>`
        }
      </div>

      <div class="observability-panel">
        <h3>Recomendaciones automáticas</h3>
        ${
          recommendations.length
            ? `<ul>${recommendations.map(item => `
                <li>${escapeHtml(item)}</li>
              `).join("")}</ul>`
            : `<p>No hay recomendaciones automáticas disponibles.</p>`
        }
      </div>

      <div class="observability-panel">
        <h3>Uso responsable</h3>
        <ul>
          <li>
            <strong>Alcance:</strong>
            ${escapeHtml(responsibleAi.decision_scope || "apoyo_documental")}
          </li>
          <li>
            <strong>Revisión humana:</strong>
            ${
              responsibleAi.human_decision_required === false
                ? "No requerida"
                : "Requerida"
            }
          </li>
          <li>
            <strong>Base válida:</strong>
            formación, experiencia, conocimientos técnicos, certificaciones y evidencia relacionada con el cargo.
          </li>
          <li>
            <strong>Variables sensibles excluidas:</strong>
            edad, género, nacionalidad, estado civil, fotografía, salud, religión u opiniones políticas.
          </li>
        </ul>
      </div>
    </div>

    <div class="observability-footer">
      <strong>Trace ID:</strong> ${escapeHtml(observability.trace_id || "No informado")}
      ${
        observability.file?.filename
          ? ` · <strong>Archivo:</strong> ${escapeHtml(observability.file.filename)}`
          : ""
      }
    </div>
  `;
}

/**
 * Renderiza la trazabilidad del agente LangChain.
 *
 * Si se usa el flujo clásico, no habrá agent_trace.
 * Si se usa LangChain, se muestran herramientas, planificación,
 * decisiones, llamadas a herramientas y memoria.
 */
function renderAgentTrace(agentTrace) {
  const container = $("agentTrace");

  if (!container) return;

  if (!agentTrace) {
    container.className = "agent-trace empty";
    container.textContent = "Este análisis fue ejecutado con el flujo clásico. No hay trazabilidad de agente.";
    return;
  }

  const tools = agentTrace.tools || [];
  const plan = agentTrace.plan || [];
  const memory = agentTrace.memory || {};
  const shortMemory = memory.short_term_memory || {};
  const decisions = shortMemory.decisions || [];
  const toolCalls = shortMemory.tool_calls || [];

  container.className = "agent-trace";

  container.innerHTML = `
    <div class="agent-summary">
      <span class="agent-badge">LangChain</span>
      <div>
        <p><strong>Framework:</strong> ${escapeHtml(agentTrace.framework || "No informado")}</p>
        <p><strong>Tipo de agente:</strong> ${escapeHtml(agentTrace.agent_type || "No informado")}</p>
        <p><strong>Modo:</strong> ${escapeHtml(agentTrace.execution_mode || "No informado")}</p>
      </div>
    </div>

    <details open>
      <summary>Planificación generada</summary>
      <pre>${escapeHtml(agentTrace.planning_output || "Sin planificación registrada.")}</pre>
    </details>

    <details>
      <summary>Herramientas declaradas (${tools.length})</summary>
      <ul>
        ${tools.map(tool => `
          <li>
            <strong>${escapeHtml(tool.name)}</strong>:
            ${escapeHtml(tool.description)}
          </li>
        `).join("")}
      </ul>
    </details>

    <details>
      <summary>Plan de ejecución (${plan.length} pasos)</summary>
      <ol>
        ${plan.map(step => `
          <li>
            <strong>${escapeHtml(step.name)}</strong>
            <br />
            <span>${escapeHtml(step.description)}</span>
            <br />
            <code>${escapeHtml(step.tool_name || "sin herramienta")}</code>
          </li>
        `).join("")}
      </ol>
    </details>

    <details>
      <summary>Decisiones adaptativas (${decisions.length})</summary>
      <ul>
        ${decisions.map(decision => `
          <li>
            <strong>${escapeHtml(decision.decision)}</strong>:
            ${escapeHtml(decision.outcome)}
          </li>
        `).join("")}
      </ul>
    </details>

    <details>
      <summary>Herramientas ejecutadas (${toolCalls.length})</summary>
      <ul>
        ${toolCalls.map(call => `
          <li>
            <strong>${escapeHtml(call.tool_name)}</strong>
            — ${call.success ? "OK" : "Error"}
            <br />
            <span>${escapeHtml(call.output_summary)}</span>
          </li>
        `).join("")}
      </ul>
    </details>

    <details>
      <summary>Memoria de largo plazo</summary>
      <p><code>${escapeHtml(memory.long_term_memory_path || "No registrada")}</code></p>
    </details>
  `;
}

/**
 * Renderiza la tabla de competencias deducidas desde el anuncio.
 */
function renderCompetencies(competencies) {
  if (!competencies.length) {
    $("competencies").className = "table-container empty";
    $("competencies").textContent = "No se detectaron competencias.";
    return;
  }

  $("competencies").className = "table-container";

  $("competencies").innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Competencia</th>
          <th>Tipo</th>
          <th>Importancia</th>
          <th>Peso</th>
          <th>Evidencia esperada</th>
          <th>Texto fuente</th>
        </tr>
      </thead>
      <tbody>
        ${competencies.map(comp => `
          <tr>
            <td><strong>${escapeHtml(comp.name)}</strong></td>
            <td>${escapeHtml(comp.category)}</td>
            <td>${escapeHtml(comp.importance)}</td>
            <td>${Math.round(comp.weight * 100)}%</td>
            <td>${escapeHtml(comp.expected_evidence)}</td>
            <td>${escapeHtml(comp.source_text || comp.reason || "")}</td>
          </tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

/**
 * Renderiza la terna recomendada.
 */
function renderTerna(terna) {
  if (!terna.length) {
    $("terna").className = "ranking-grid empty";
    $("terna").textContent = "Sin terna generada.";
    return;
  }

  $("terna").className = "ranking-grid";

  $("terna").innerHTML = terna.map((candidate, index) => `
    <article class="candidate-card">
      <span class="badge">Lugar ${index + 1}</span>
      <h3>${escapeHtml(candidate.candidate_name)}</h3>
      <div class="score">${candidate.normalized_score}</div>
      <p><strong>Resultado:</strong> ${escapeHtml(candidate.recommendation)}</p>
      <p><strong>Fortalezas:</strong> ${(candidate.strengths || []).map(escapeHtml).join(", ") || "No evidenciadas"}</p>
      <p><strong>Brechas:</strong> ${(candidate.gaps || []).map(escapeHtml).join(", ") || "Sin brechas críticas"}</p>
    </article>
  `).join("");
}

/**
 * Renderiza el ranking completo de candidatos.
 */
function renderRanking(ranking) {
  if (!ranking.length) {
    $("ranking").className = "ranking-list empty";
    $("ranking").textContent = "Sin ranking generado.";
    return;
  }

  $("ranking").className = "ranking-list";

  $("ranking").innerHTML = ranking.map((candidate, index) => `
    <article class="candidate-card">
      <span class="badge">#${index + 1}</span>
      <h3>${escapeHtml(candidate.candidate_name)} · ${candidate.normalized_score}/100</h3>
      <p><strong>Recomendación:</strong> ${escapeHtml(candidate.recommendation)}</p>
      <div class="details">
        ${(candidate.evaluations || []).map(ev => `
          <section>
            <h4>
              ${escapeHtml(ev.competency?.name || "")}
              —
              ${escapeHtml(ev.evidence_level)}
              (${ev.evidence_score}/4)
            </h4>
            <p>${escapeHtml(ev.explanation)}</p>
            ${(ev.evidences || []).slice(0, 2).map(evidence => `
              <div class="evidence">
                <strong>Evidencia:</strong> ${escapeHtml(evidence.text)}
              </div>
            `).join("")}
          </section>
        `).join("")}
      </div>
    </article>
  `).join("");
}

/**
 * Actualiza el mensaje de estado general de la aplicación.
 */
function setStatus(message) {
  const status = $("status");

  if (!status) {
    console.warn("No se encontró el elemento #status:", message);
    return;
  }

  status.textContent = message || "";
}

/**
 * Escapa texto antes de insertarlo como HTML.
 *
 * Esto evita que valores externos rompan el DOM o inyecten HTML.
 * Además conserva valores válidos como 0, 0.0 y false.
 */
function escapeHtml(value) {
  if (value === null || value === undefined) return "";

  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

// ----------------------------------------------------------------------
// Registro de eventos de botones y controles
// ----------------------------------------------------------------------

const extractBtn = $("extractBtn");

if (extractBtn) {
  extractBtn.addEventListener("click", () => {
    extractAnnouncementText().catch(error => setStatus(error.message));
  });
}

const analyzeBtn = $("analyzeBtn");

if (analyzeBtn) {
  analyzeBtn.addEventListener("click", () => {
    analyze().catch(error => setStatus(error.message));
  });
}

const modelSelect = $("modelSelect");

if (modelSelect) {
  modelSelect.addEventListener("change", updateModelDescription);
}

const useAgentFlow = $("useAgentFlow");

if (useAgentFlow) {
  useAgentFlow.addEventListener("change", () => {
    const endpoints = getAnalysisEndpoints();
    setStatus(`Modo seleccionado: ${endpoints.label}.`);
  });
}

// ----------------------------------------------------------------------
// Carga inicial de la aplicación
// ----------------------------------------------------------------------
//
// Se ejecutan en paralelo:
// - estado del LLM;
// - catálogo de modelos;
// - archivos disponibles.
//
// Cada carga maneja su propio error para que una falla parcial no bloquee
// toda la interfaz.
Promise.all([
  loadLLMStatus().catch(error => {
    console.error(error);
    renderLLMStatus(null);
  }),

  loadModelCatalog().catch(error => {
    console.error(error);
    setStatus("No se pudo cargar el catálogo de modelos. Se usará el modelo por defecto.");
  }),

  loadFiles().catch(error => setStatus(error.message))
]);