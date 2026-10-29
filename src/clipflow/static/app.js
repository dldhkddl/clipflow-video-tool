const $ = (selector) => document.querySelector(selector);

const state = {
  projectId: null,
  workspace: null,
  costApproved: false,
  higgsfield: null,
  meta: null,
  sourceMode: "higgsfield",
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const body = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail = body && typeof body === "object" ? (body.error || body.detail) : body;
    const message = body?.error?.message || (typeof detail === "string"
      ? detail
      : detail?.message) || "요청을 처리하지 못했습니다.";
    throw new Error(message);
  }
  return body;
}

function say(message, kind = "info") {
  const box = $("#status");
  box.textContent = message;
  box.dataset.kind = kind;
}

function setCopyAnalysisStatus(message = "", kind = "info") {
  const box = $("#copyAnalysisStatus");
  if (!box) return;
  box.textContent = message;
  box.dataset.kind = kind;
  box.hidden = !message;
}

function setBusy(button, busy, text) {
  if (!button) return;
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = text;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.disabled = false;
  }
}

function renderHiggsfieldStatus(status) {
  const chip = $("#higgsfieldCredit");
  const value = $("#higgsfieldCreditValue");
  state.higgsfield = status;
  if (status.offline_demo) {
    chip.dataset.state = "demo";
    value.textContent = "데모 모드";
    chip.title = status.message || "실제 크레딧을 사용하지 않습니다.";
    return;
  }
  const connected = Boolean(status.available && status.authenticated && status.workspace_selected);
  chip.dataset.state = connected ? "connected" : "error";
  value.textContent = connected
    ? (status.credits == null ? "연결됨" : `${status.credits.toLocaleString("ko-KR")} 크레딧`)
    : "연결 필요";
  chip.title = status.message || value.textContent;
}

function renderMetaStatus(status) {
  const chip = $("#metaConnection");
  const value = $("#metaConnectionValue");
  state.meta = status;
  chip.dataset.state = status.connected ? "connected" : "error";
  value.textContent = status.connected ? `@${status.username || "연결됨"}` : "연결 필요";
  chip.title = status.error || (status.connected ? "Instagram 게시 가능" : "Meta 연결 정보를 확인하세요.");
  updatePublishButton();
}

function setSourceMode(mode) {
  state.sourceMode = mode;
  const isHiggsfield = mode === "higgsfield";
  $("#higgsfieldSourcePane").hidden = !isHiggsfield;
  $("#uploadSourcePane").hidden = isHiggsfield;
  $("#sourceModeHiggsfield").classList.toggle("active", isHiggsfield);
  $("#sourceModeUpload").classList.toggle("active", !isHiggsfield);
  $("#sourceModeHiggsfield").setAttribute("aria-selected", String(isHiggsfield));
  $("#sourceModeUpload").setAttribute("aria-selected", String(!isHiggsfield));
}

function videoPayload(confirmedCost = false) {
  return {
    kind: "video",
    prompt: $("#aiPrompt").value.trim(),
    start_asset_id: $("#startAsset").value || null,
    confirmed_cost: confirmedCost,
    use_video_profile: false,
    use_profile_reference: $("#useProfileReference").checked,
    video_model: $("#videoModel").value,
    video_duration: Number($("#videoDuration").value),
    video_aspect_ratio: $("#videoAspectRatio").value,
    video_resolution: $("#videoResolution").value,
    video_bitrate_mode: $("#videoBitrateMode").value,
    video_generate_audio: $("#videoGenerateAudio").checked,
  };
}

function validatePrompt() {
  if (!state.projectId) throw new Error("먼저 프로젝트를 준비해 주세요.");
  if ($("#aiPrompt").value.trim().length < 3) throw new Error("영상 프롬프트를 입력해 주세요.");
}

function invalidateCost() {
  state.costApproved = false;
  $("#generateVideoButton").disabled = true;
  $("#costResult").textContent = "설정이 바뀌었습니다. 생성 비용을 다시 확인해 주세요.";
}

async function loadProjects(preferredId = null) {
  let projects = await api("/api/projects");
  if (!projects.length) {
    const stamp = new Date().toLocaleDateString("ko-KR", { month: "2-digit", day: "2-digit" });
    const created = await api("/api/projects", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: `줄라이 릴스 ${stamp}`,
        affiliate_url: "https://example.com",
        product_name: "줄라이 릴스",
        category: "강아지 콘텐츠",
        features: ["캐릭터 중심 숏폼"],
        target_seconds: 15,
        platforms: ["instagram"],
      }),
    });
    projects = [created];
  }
  const select = $("#projectSelect");
  select.innerHTML = projects.map((project) => `<option value="${project.id}">${escapeHtml(project.name)}</option>`).join("");
  const saved = localStorage.getItem("clipflowProjectId");
  const selected = projects.find((project) => project.id === preferredId)
    || projects.find((project) => project.id === saved)
    || projects[0];
  select.value = selected.id;
  await loadWorkspace(selected.id);
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value == null ? "" : String(value);
  return div.innerHTML;
}

async function loadWorkspace(projectId) {
  state.projectId = projectId;
  localStorage.setItem("clipflowProjectId", projectId);
  state.workspace = await api(`/api/projects/${projectId}/workspace`);
  const savedPrompt = localStorage.getItem(`clipflowPrompt:${projectId}`);
  if (savedPrompt !== null) $("#aiPrompt").value = savedPrompt;
  renderVideo(state.workspace.video_url);
  await Promise.all([loadSocialCopy(), loadInstagramStatus()]);
  state.costApproved = false;
  $("#generateVideoButton").disabled = true;
  $("#costResult").textContent = "생성 전 비용을 먼저 확인해 주세요.";
  say(`‘${state.workspace.project.name}’ 프로젝트를 불러왔습니다.`, "success");
}

function renderVideo(url) {
  const video = $("#latestVideo");
  const empty = $("#videoEmpty");
  if (!url) {
    video.pause();
    video.removeAttribute("src");
    video.hidden = true;
    empty.hidden = false;
  } else {
    video.src = `${url}?v=${Date.now()}`;
    video.hidden = false;
    empty.hidden = true;
    video.load();
  }
  updatePublishButton();
}

async function loadReference() {
  const status = await api("/api/video-profile/reference-image/status");
  const text = $("#videoProfileReferenceStatus");
  const preview = $("#videoProfileReferencePreview");
  if (status.exists) {
    text.textContent = "저장된 기준 이미지를 영상에 사용할 수 있습니다.";
    preview.src = `${status.media_url}?v=${Date.now()}`;
    preview.hidden = false;
  } else {
    text.textContent = "강아지 얼굴과 전신이 선명한 이미지 한 장을 저장해 주세요.";
    preview.hidden = true;
  }
}

async function uploadReference() {
  const input = $("#videoProfileReferenceFile");
  if (!input.files?.length) throw new Error("저장할 기준 이미지를 선택해 주세요.");
  const data = new FormData();
  data.append("file", input.files[0]);
  await api("/api/video-profile/reference-image", { method: "POST", body: data });
  await loadReference();
  invalidateCost();
  say("캐릭터 기준 이미지를 저장했습니다.", "success");
}

async function checkCost() {
  validatePrompt();
  const button = $("#costButton");
  setBusy(button, true, "비용 확인 중…");
  try {
    const result = await api(`/api/projects/${state.projectId}/ai-assets/cost`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(videoPayload(false)),
    });
    const credits = result.credits == null ? "계산 불가" : `${result.credits.toLocaleString("ko-KR")} 크레딧`;
    const mode = result.offline_demo ? " · 데모 모드(과금 없음)" : "";
    $("#costResult").textContent = `예상 생성 비용: ${credits}${mode}. 생성 버튼을 누르면 이 비용에 동의한 것으로 처리됩니다.`;
    state.costApproved = true;
    $("#generateVideoButton").disabled = false;
    say("비용을 확인했습니다. 이제 영상을 생성할 수 있습니다.", "success");
  } finally {
    setBusy(button, false);
  }
}

async function generateVideo() {
  validatePrompt();
  if (!state.costApproved) throw new Error("생성 비용을 먼저 확인해 주세요.");
  const button = $("#generateVideoButton");
  setBusy(button, true, "힉스필드 생성 중…");
  say("힉스필드가 영상을 만들고 있습니다. 창을 닫지 말고 기다려 주세요.");
  try {
    await api(`/api/projects/${state.projectId}/ai-assets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(videoPayload(true)),
    });
    state.workspace = await api(`/api/projects/${state.projectId}/workspace`);
    renderVideo(state.workspace.video_url);
    await generateSocialCopy(false);
    await loadInstagramStatus();
    $("#latestVideo").scrollIntoView({ behavior: "smooth", block: "center" });
    $("#latestVideo").play().catch(() => {});
    say("영상과 게시 문구를 만들었습니다. 확인 후 바로 게시할 수 있습니다.", "success");
  } finally {
    state.costApproved = false;
    setBusy(button, false);
    button.disabled = true;
  }
}

async function uploadOwnVideo() {
  if (!state.projectId) throw new Error("먼저 프로젝트를 준비해 주세요.");
  const input = $("#ownVideoFile");
  const file = input.files?.[0];
  if (!file) throw new Error("업로드할 MP4 영상을 선택해 주세요.");
  if (file.type !== "video/mp4" || !file.name.toLowerCase().endsWith(".mp4")) {
    throw new Error("MP4 영상만 업로드할 수 있습니다.");
  }
  if (file.size > 100 * 1024 * 1024) throw new Error("영상은 100MB 이하여야 합니다.");

  const button = $("#uploadOwnVideoButton");
  setBusy(button, true, "영상 업로드 중…");
  say("영상을 업로드하고 있습니다.");
  try {
    const data = new FormData();
    data.append("file", file);
    await api(`/api/projects/${state.projectId}/videos/upload`, {
      method: "POST",
      body: data,
    });
    state.workspace = await api(`/api/projects/${state.projectId}/workspace`);
    renderVideo(state.workspace.video_url);
    await Promise.all([loadSocialCopy(), loadInstagramStatus()]);
    say("영상 업로드를 마쳤습니다. 화면과 음성·대사를 분석하고 있습니다.");
    await generateSocialCopy(false);
    $("#latestVideo").scrollIntoView({ behavior: "smooth", block: "center" });
    say("영상과 게시 문구를 준비했습니다. 내용을 고친 뒤 바로 게시할 수 있습니다.", "success");
  } finally {
    setBusy(button, false);
  }
}

function hashtagsFromInput() {
  return $("#socialHashtags").value.split(/[\s,]+/).map((value) => value.trim()).filter(Boolean);
}

async function loadSocialCopy() {
  const copy = await api(`/api/projects/${state.projectId}/social-copy`);
  $("#socialDescription").value = copy.description || "";
  $("#socialHashtags").value = (copy.hashtags || []).join(" ");
}

async function saveSocialCopy(announce = true) {
  const saved = await api(`/api/projects/${state.projectId}/social-copy`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      description: $("#socialDescription").value.trim(),
      hashtags: hashtagsFromInput(),
    }),
  });
  $("#socialDescription").value = saved.description;
  $("#socialHashtags").value = saved.hashtags.join(" ");
  if (announce) say("Instagram 설명과 해시태그를 저장했습니다.", "success");
}

async function generateSocialCopy(announce = true) {
  if (!state.projectId) throw new Error("먼저 프로젝트를 준비해 주세요.");
  const prompt = $("#aiPrompt").value.trim();
  if (!state.workspace?.video_url && prompt.length < 3) {
    throw new Error("분석할 영상이나 영상 프롬프트가 필요합니다.");
  }
  const button = $("#generateSocialCopyButton");
  setBusy(button, true, "영상 분석 중…");
  setCopyAnalysisStatus("영상을 업로드해 화면·음성·대사를 분석하고 있습니다. 잠시 기다려 주세요.");
  try {
    const copy = await api(`/api/projects/${state.projectId}/social-copy/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, image_asset_id: null }),
    });
    $("#socialDescription").value = copy.description;
    $("#socialHashtags").value = copy.hashtags.join(" ");
    setCopyAnalysisStatus("영상 분석이 끝났습니다. 생성된 문구를 확인해 주세요.", "success");
    if (announce) say("영상에 맞는 설명과 해시태그를 만들었습니다.", "success");
  } catch (error) {
    setCopyAnalysisStatus(error.message || "영상 분석에 실패했습니다.", "error");
    throw error;
  } finally {
    setBusy(button, false);
  }
}

function updatePublishButton() {
  const button = $("#instagramPublishButton");
  if (!button) return;
  const hasVideo = Boolean(state.workspace?.video_url);
  button.disabled = !(hasVideo && state.meta?.connected);
}

function publicationText(publication) {
  const values = {
    not_prepared: "영상 확인 후 바로 게시할 수 있습니다.",
    in_progress: "Instagram에서 영상을 처리하고 있습니다.",
    finished: "처리가 끝났습니다. 게시를 완료합니다.",
    published: "Instagram Reels 게시를 완료했습니다.",
    error: publication.error || "Instagram 처리 중 오류가 발생했습니다.",
  };
  return values[publication.status] || "게시 준비 상태를 확인했습니다.";
}

async function loadInstagramStatus() {
  if (!state.projectId) return;
  const publication = await api(`/api/projects/${state.projectId}/instagram/status`);
  $("#instagramPublicationStatus").textContent = publicationText(publication);
  const button = $("#instagramPublishButton");
  if (publication.status === "published") {
    button.textContent = "게시 완료";
    button.disabled = true;
  } else {
    button.textContent = "Instagram에 바로 게시";
    updatePublishButton();
  }
  return publication;
}

async function publishToInstagram() {
  if (!state.meta?.connected) throw new Error("Instagram 연결을 먼저 확인해 주세요.");
  if (!state.workspace?.video_url) throw new Error("게시할 영상이 없습니다.");
  await saveSocialCopy(false);
  const button = $("#instagramPublishButton");
  setBusy(button, true, "게시 준비 중…");
  try {
    let publication = await loadInstagramStatus();
    if (publication.status === "not_prepared" || publication.status === "error") {
      publication = await api(`/api/projects/${state.projectId}/instagram/prepare`, { method: "POST" });
    }
    for (let attempt = 0; attempt < 24 && publication.status === "in_progress"; attempt += 1) {
      $("#instagramPublicationStatus").textContent = `Instagram 영상 처리 중… (${attempt + 1}/24)`;
      await new Promise((resolve) => setTimeout(resolve, 5000));
      publication = await api(`/api/projects/${state.projectId}/instagram/status`);
    }
    if (publication.status !== "finished") {
      if (publication.status === "published") return;
      throw new Error(publication.error || "Instagram 영상 처리가 아직 끝나지 않았습니다. 잠시 후 다시 눌러 주세요.");
    }
    publication = await api(`/api/projects/${state.projectId}/instagram/publish`, { method: "POST" });
    $("#instagramPublicationStatus").textContent = publicationText(publication);
    say("Instagram Reels에 게시했습니다.", "success");
  } finally {
    setBusy(button, false);
    await loadInstagramStatus().catch(() => {});
  }
}

async function createProject() {
  const name = window.prompt("새 프로젝트 이름을 입력해 주세요.", "줄라이 새 에피소드");
  if (!name?.trim()) return;
  const project = await api("/api/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: name.trim(), affiliate_url: "https://example.com", product_name: "줄라이 릴스",
      category: "강아지 콘텐츠", features: ["캐릭터 중심 숏폼"], target_seconds: 15,
      platforms: ["instagram"],
    }),
  });
  $("#aiPrompt").value = "";
  await loadProjects(project.id);
}

function openDeleteProjectDialog() {
  if (!state.projectId) return;
  const name = state.workspace?.project?.name || "현재 프로젝트";
  const dialog = $("#deleteProjectDialog");
  dialog.dataset.projectId = state.projectId;
  $("#deleteProjectMessage").textContent = `‘${name}’ 프로젝트와 저장된 영상 파일이 삭제됩니다. 이 작업은 되돌릴 수 없습니다.`;
  dialog.showModal();
}

async function confirmDeleteProject() {
  const dialog = $("#deleteProjectDialog");
  const projectId = dialog.dataset.projectId;
  if (!projectId) return;
  const button = $("#confirmDeleteProjectButton");
  setBusy(button, true, "삭제 중…");
  try {
    await api(`/api/projects/${projectId}`, { method: "DELETE" });
    localStorage.removeItem(`clipflowPrompt:${projectId}`);
    localStorage.removeItem("clipflowProjectId");
    dialog.close();
    await loadProjects();
    say("프로젝트를 삭제했습니다.", "success");
  } finally {
    setBusy(button, false);
  }
}

function bindEvents() {
  $("#loadProjectButton").addEventListener("click", () => loadWorkspace($("#projectSelect").value).catch(handleError));
  $("#projectSelect").addEventListener("change", () => loadWorkspace($("#projectSelect").value).catch(handleError));
  $("#newProjectButton").addEventListener("click", () => createProject().catch(handleError));
  $("#deleteProjectButton").addEventListener("click", openDeleteProjectDialog);
  $("#cancelDeleteProjectButton").addEventListener("click", () => $("#deleteProjectDialog").close());
  $("#confirmDeleteProjectButton").addEventListener("click", () => confirmDeleteProject().catch(handleError));
  $("#sourceModeHiggsfield").addEventListener("click", () => setSourceMode("higgsfield"));
  $("#sourceModeUpload").addEventListener("click", () => setSourceMode("upload"));
  $("#uploadVideoProfileReferenceButton").addEventListener("click", () => uploadReference().catch(handleError));
  $("#ownVideoFile").addEventListener("change", (event) => {
    const file = event.target.files?.[0];
    $("#ownVideoFileName").textContent = file
      ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(1)}MB`
      : "선택된 영상이 없습니다.";
  });
  $("#uploadOwnVideoButton").addEventListener("click", () => uploadOwnVideo().catch(handleError));
  $("#costButton").addEventListener("click", () => checkCost().catch(handleError));
  $("#generateVideoButton").addEventListener("click", () => generateVideo().catch(handleError));
  $("#generateSocialCopyButton").addEventListener("click", () => generateSocialCopy().catch(handleError));
  $("#saveSocialCopyButton").addEventListener("click", () => saveSocialCopy().catch(handleError));
  $("#instagramPublishButton").addEventListener("click", () => publishToInstagram().catch(handleError));
  $("#aiPrompt").addEventListener("input", () => {
    if (state.projectId) localStorage.setItem(`clipflowPrompt:${state.projectId}`, $("#aiPrompt").value);
    invalidateCost();
  });
  ["#useProfileReference", "#videoModel", "#videoDuration", "#videoAspectRatio", "#videoResolution", "#videoBitrateMode", "#videoGenerateAudio"]
    .forEach((selector) => $(selector).addEventListener("change", invalidateCost));
}

function handleError(error) {
  console.error(error);
  say(error.message || "요청을 처리하지 못했습니다.", "error");
}

async function initialize() {
  bindEvents();
  try {
    const [higgsfieldStatus, metaStatus] = await Promise.all([
      api("/api/higgsfield/status"), api("/api/meta/status"), loadReference(),
    ]);
    renderHiggsfieldStatus(higgsfieldStatus);
    renderMetaStatus(metaStatus);
    await loadProjects();
  } catch (error) {
    handleError(error);
  }
}

initialize();
