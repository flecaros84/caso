const state = {
  announcements: [],
  cvs: [],
  llmStatus: null,
  models: [],
  defaultModel: null,
  currentJobId: null,
  progressTimer: null
};

const $ = (id) => document.getElementById(id);

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


async function loadLLMStatus() {
  const data = await api("/api/llm/status");
  state.llmStatus = data;
  renderLLMStatus(data);
}

async function loadModelCatalog() {
  const data = await api("/api/llm/models");
  state.models = data.models || [];
  state.defaultModel = data.default_model || null;
  renderModelSelector();
}

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
    if (description) description.textContent = "No se encontró catálogo local. Se usará el modelo configurado por defecto.";
    return;
  }

  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = `${model.name || model.id} — ${model.id}`;
    if (model.id === state.defaultModel || model.default) {
      option.selected = true;
    }
    select.appendChild(option);
  }

  updateModelDescription();
}

function getSelectedModel() {
  const select = $("modelSelect");
  return select?.value || state.defaultModel || state.llmStatus?.model || null;
}

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

async function loadFiles() {
  setStatus("Cargando archivos disponibles...");
  const data = await api("/api/files");
  state.announcements = data.announcements || [];
  state.cvs = data.cvs || [];
  renderFileSelectors();
  setStatus("Archivos cargados.");
}

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
    label.innerHTML = `
      <input type="checkbox" value="${cv.id}" checked />
      <span>${cv.filename}</span>
    `;
    cvList.appendChild(label);
  }
}

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

  clearPreviousResults();
  showProgressPanel();
  updateProgressUI({
    status: "queued",
    progress: 0,
    current_step: "Enviando análisis al backend...",
    llm_success: 0,
    llm_fallback: 0,
    llm_errors: 0,
    events: [],
    event_count: 0
  });
  setStatus("Análisis iniciado. Puedes seguir el avance en la barra de progreso.");
  $("analyzeBtn").disabled = true;

  try {
    const startResponse = await api("/api/analyze/start", {
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
    await pollAnalysisProgress(state.currentJobId);
  } finally {
    $("analyzeBtn").disabled = false;
  }
}

async function pollAnalysisProgress(jobId) {
  return new Promise((resolve, reject) => {
    if (state.progressTimer) {
      clearInterval(state.progressTimer);
    }

    const tick = async () => {
      try {
        const status = await api(`/api/analyze/status/${jobId}`);
        updateProgressUI(status);

        if (status.status === "completed") {
          clearInterval(state.progressTimer);
          state.progressTimer = null;

          const result = await api(`/api/analyze/result/${jobId}`);
          renderCompetencies(result.competencies || []);
          renderTerna(result.terna || []);
          renderRanking(result.ranking || []);
          renderReport(result.report || null);
          if (result.llm_status) {
            renderLLMStatus(result.llm_status);
          }
          setStatus("Análisis completado.");
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

    tick();
    state.progressTimer = setInterval(tick, 1500);
  });
}

function showProgressPanel() {
  $("progressPanel").classList.remove("hidden");
}

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
  panel.classList.add(status.status === "completed" ? "completed" : status.status === "failed" ? "failed" : "running");

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
  if (status.status !== "completed") {
    eventsContainer.scrollTop = eventsContainer.scrollHeight;
  }
}

function clearPreviousResults() {
  $("competencies").className = "table-container empty";
  $("competencies").textContent = "Análisis en curso...";
  $("terna").className = "ranking-grid empty";
  $("terna").textContent = "Análisis en curso...";
  $("ranking").className = "ranking-list empty";
  $("ranking").textContent = "Análisis en curso...";
  $("report").className = "report-box empty";
  $("report").textContent = "El reporte se generará al finalizar el análisis...";
}

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
      <p><strong>Fortalezas:</strong> ${candidate.strengths.map(escapeHtml).join(", ") || "No evidenciadas"}</p>
      <p><strong>Brechas:</strong> ${candidate.gaps.map(escapeHtml).join(", ") || "Sin brechas críticas"}</p>
    </article>
  `).join("");
}

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
        ${candidate.evaluations.map(ev => `
          <section>
            <h4>${escapeHtml(ev.competency.name)} — ${escapeHtml(ev.evidence_level)} (${ev.evidence_score}/4)</h4>
            <p>${escapeHtml(ev.explanation)}</p>
            ${ev.evidences.slice(0, 2).map(evidence => `
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

function setStatus(message) {
  $("status").textContent = message;
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

$("extractBtn").addEventListener("click", () => extractAnnouncementText().catch(error => setStatus(error.message)));
$("analyzeBtn").addEventListener("click", () => analyze().catch(error => setStatus(error.message)));
$("modelSelect")?.addEventListener("change", updateModelDescription);

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
