/**
 * Zero-Trust Edge Video - Dashboard Interaction Controller
 * Handles user controls, file uploads, state synchronization, telemetry updates, and activity logs.
 */

document.addEventListener("DOMContentLoaded", () => {
  initDashboard();
});

let isPipelineRunning = false;

function initDashboard() {
  // Bind Input Source buttons (File vs Webcam)
  document.querySelectorAll(".btn-source").forEach(btn => {
    btn.addEventListener("click", () => {
      const src = btn.dataset.source;
      configurePipeline({ input_source: src });
    });
  });

  // Bind Device buttons (GPU vs CPU)
  document.querySelectorAll(".btn-device").forEach(btn => {
    btn.addEventListener("click", () => {
      const dev = btn.dataset.device;
      configurePipeline({ device: dev });
    });
  });

  // Bind Privacy Overlays buttons (Show / Hide Labels)
  document.querySelectorAll(".btn-label").forEach(btn => {
    btn.addEventListener("click", () => {
      const showLabels = btn.dataset.labels === "true";
      configurePipeline({ show_labels: showLabels });
    });
  });

  // Bind Policy selection cards
  document.querySelectorAll(".policy-option-card").forEach(card => {
    card.addEventListener("click", () => {
      const policyName = card.dataset.policy;
      configurePipeline({ policy: policyName });
    });
  });

  // Bind Start / Stop action buttons
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  if (btnStart) btnStart.addEventListener("click", startPipeline);
  if (btnStop) btnStop.addEventListener("click", stopPipeline);

  // Bind Custom Video File Upload
  const btnUploadTrigger = document.getElementById("btnUploadTrigger");
  const videoFileInput = document.getElementById("videoFileInput");
  if (btnUploadTrigger && videoFileInput) {
    btnUploadTrigger.addEventListener("click", () => {
      videoFileInput.click();
    });
    videoFileInput.addEventListener("change", handleFileUpload);
  }

  // Bind Video Selection Dropdown
  const videoSelectDropdown = document.getElementById("videoSelectDropdown");
  if (videoSelectDropdown) {
    videoSelectDropdown.addEventListener("change", (e) => {
      const selectedPath = e.target.value;
      if (selectedPath) {
        configurePipeline({ input_file: selectedPath, input_source: "file" });
      }
    });
  }

  // Bind monitor feed error recovery
  const monitorImg = document.getElementById("monitorFeed");
  if (monitorImg) {
    monitorImg.addEventListener("error", () => {
      setTimeout(() => {
        if (monitorImg) {
          monitorImg.src = "/video_feed?t=" + new Date().getTime();
        }
      }, 1000);
    });
  }

  // Initial video files list fetch
  loadVideoList();

  // Initialize interactive drawing & analytics toggles
  initAnalyticsDrawingController();

  // Initialize Stage B: Crowd Widget and Crypto Inspector
  initCrowdWidget();
  initCryptoInspector();

  // Initial synchronization and recurring poll
  fetchStatus();
  setInterval(fetchStatus, 800);
}

function handleFileUpload(e) {
  const file = e.target.files && e.target.files[0];
  if (!file) return;

  const btnUploadTrigger = document.getElementById("btnUploadTrigger");
  const uploadProgressBar = document.getElementById("uploadProgressBar");
  const uploadProgressFill = document.getElementById("uploadProgressFill");
  const uploadStatusText = document.getElementById("uploadStatusText");

  if (btnUploadTrigger) btnUploadTrigger.disabled = true;
  if (uploadProgressBar) uploadProgressBar.style.display = "block";
  if (uploadProgressFill) uploadProgressFill.style.width = "25%";
  if (uploadStatusText) uploadStatusText.innerText = "Uploading " + file.name + "...";

  const formData = new FormData();
  formData.append("video", file);

  const xhr = new XMLHttpRequest();
  xhr.open("POST", "/api/upload", true);

  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable && uploadProgressFill) {
      const percent = Math.round((event.loaded / event.total) * 90);
      uploadProgressFill.style.width = percent + "%";
    }
  };

  xhr.onload = () => {
    if (btnUploadTrigger) btnUploadTrigger.disabled = false;
    if (uploadProgressFill) uploadProgressFill.style.width = "100%";

    if (xhr.status === 200) {
      try {
        const res = JSON.parse(xhr.responseText);
        if (uploadStatusText) {
          uploadStatusText.innerText = `Loaded: ${res.filename} (${res.size_mb} MB)`;
        }
        // Refresh available videos list and state
        loadVideoList();
        if (res.state) {
          updateUIState(res.state);
        }
        refreshStream();
      } catch (err) {
        console.error("Error parsing upload response:", err);
      }
    } else {
      let errMsg = "Upload failed";
      try {
        const errObj = JSON.parse(xhr.responseText);
        if (errObj.error) errMsg = errObj.error;
      } catch (_) {}
      if (uploadStatusText) uploadStatusText.innerText = errMsg;
    }

    setTimeout(() => {
      if (uploadProgressBar) uploadProgressBar.style.display = "none";
      if (uploadProgressFill) uploadProgressFill.style.width = "0%";
    }, 1200);

    // Reset input
    e.target.value = "";
  };

  xhr.onerror = () => {
    if (btnUploadTrigger) btnUploadTrigger.disabled = false;
    if (uploadStatusText) uploadStatusText.innerText = "Network error during upload.";
    if (uploadProgressBar) uploadProgressBar.style.display = "none";
    e.target.value = "";
  };

  xhr.send(formData);
}

function loadVideoList() {
  fetch("/api/videos")
    .then(r => r.json())
    .then(data => {
      if (!data || !data.videos) return;
      const dropdown = document.getElementById("videoSelectDropdown");
      const wrap = document.getElementById("videoSelectorWrap");
      if (!dropdown) return;

      dropdown.innerHTML = "";
      data.videos.forEach(vid => {
        const opt = document.createElement("option");
        opt.value = vid.path;
        opt.innerText = vid.name;
        if (vid.path === data.active_file) {
          opt.selected = true;
        }
        dropdown.appendChild(opt);
      });

      // Show dropdown if more than 1 video is available
      if (wrap) {
        wrap.style.display = data.videos.length > 1 ? "block" : "none";
      }

      // Update active file label
      const activeNameSpan = document.getElementById("activeFileName");
      if (activeNameSpan && data.active_file) {
        const baseName = data.active_file.split("/").pop();
        activeNameSpan.innerText = baseName.length > 20 ? baseName.substring(0, 18) + "..." : baseName;
        activeNameSpan.title = data.active_file;
      }
    })
    .catch(err => console.error("Error loading video list:", err));
}

function configurePipeline(params) {
  fetch("/api/configure", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params)
  })
    .then(r => r.json())
    .then(data => {
      if (data && data.state) {
        updateUIState(data.state);
        loadVideoList();
      }
    })
    .catch(err => console.error("Configure error:", err));
}

function startPipeline() {
  const btnStart = document.getElementById("btnStart");
  if (btnStart) btnStart.disabled = true;

  fetch("/api/start", { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (btnStart) btnStart.disabled = false;
      if (data && data.state) {
        updateUIState(data.state);
      }
    })
    .catch(err => {
      if (btnStart) btnStart.disabled = false;
      console.error("Start error:", err);
    });
}

function stopPipeline() {
  const btnStop = document.getElementById("btnStop");
  if (btnStop) btnStop.disabled = true;

  fetch("/api/stop", { method: "POST" })
    .then(r => r.json())
    .then(data => {
      if (btnStop) btnStop.disabled = false;
      if (data && data.state) {
        updateUIState(data.state);
      }
    })
    .catch(err => {
      if (btnStop) btnStop.disabled = false;
      console.error("Stop error:", err);
    });
}

function refreshStream() {
  const monitorImg = document.getElementById("monitorFeed");
  const standbyScreen = document.getElementById("standbyScreen");

  if (isPipelineRunning) {
    if (standbyScreen) standbyScreen.style.display = "none";
    if (monitorImg) {
      monitorImg.style.display = "block";
      monitorImg.src = "/video_feed?t=" + new Date().getTime();
    }
  } else {
    if (standbyScreen) standbyScreen.style.display = "none";
    if (monitorImg) {
      monitorImg.style.display = "block";
      monitorImg.src = "/video_feed?t=" + new Date().getTime();
    }
  }
}

function fetchStatus() {
  fetch("/api/status")
    .then(r => r.json())
    .then(data => {
      updateUIState(data);
      if (data.logs) {
        renderLogs(data.logs);
      }
    })
    .catch(() => {});
}

function updateUIState(state) {
  if (!state) return;

  const wasRunning = isPipelineRunning;
  isPipelineRunning = state.status === "RUNNING";

  if (wasRunning !== isPipelineRunning) {
    refreshStream();
  }

  // Top header status badge
  const statusPill = document.getElementById("statusPill");
  const statusText = document.getElementById("statusText");
  const bannerStatus = document.getElementById("summaryStatus");
  if (statusPill && statusText) {
    if (isPipelineRunning) {
      statusPill.className = "status-pill running";
      statusText.innerText = "PIPELINE ACTIVE";
      if (bannerStatus) bannerStatus.innerText = "RUNNING";
    } else {
      statusPill.className = "status-pill stopped";
      statusText.innerText = "STANDBY";
      if (bannerStatus) bannerStatus.innerText = "STOPPED";
    }
  }

  // Action buttons enable/disable
  const btnStart = document.getElementById("btnStart");
  const btnStop = document.getElementById("btnStop");
  if (btnStart) btnStart.disabled = isPipelineRunning;
  if (btnStop) btnStop.disabled = !isPipelineRunning;

  // Selected summaries strip
  const sumInput = document.getElementById("summaryInput");
  const sumDevice = document.getElementById("summaryDevice");
  const sumPolicy = document.getElementById("summaryPolicy");

  const currentFile = state.input_file ? state.input_file.split("/").pop() : "video.mp4";
  if (sumInput) {
    sumInput.innerText = state.input_source === "webcam" ? "WEBCAM" : `FILE (${currentFile})`;
  }
  if (sumDevice) sumDevice.innerText = state.device || "GPU";
  if (sumPolicy) sumPolicy.innerText = state.policy || "STANDARD";

  // Active file badge in control panel
  const activeNameSpan = document.getElementById("activeFileName");
  if (activeNameSpan) {
    activeNameSpan.innerText = currentFile.length > 20 ? currentFile.substring(0, 18) + "..." : currentFile;
    activeNameSpan.title = state.input_file || "video.mp4";
  }

  // Sync dropdown selection if visible
  const dropdown = document.getElementById("videoSelectDropdown");
  if (dropdown && state.input_file) {
    dropdown.value = state.input_file;
  }

  // Input source active buttons
  document.querySelectorAll(".btn-source").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.source === state.input_source);
  });

  // Device active buttons
  document.querySelectorAll(".btn-device").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.device === state.device);
  });

  // Overlay labels active buttons
  document.querySelectorAll(".btn-label").forEach(btn => {
    const isTrue = btn.dataset.labels === "true";
    btn.classList.toggle("active", isTrue === Boolean(state.show_labels));
  });

  // Policy selected cards
  document.querySelectorAll(".policy-option-card").forEach(card => {
    card.classList.toggle("selected", card.dataset.policy === state.policy);
  });

  // Performance telemetry
  const valFps = document.getElementById("valFps");
  const valLatency = document.getElementById("valLatency");
  const valFrames = document.getElementById("valFrames");
  const valFaces = document.getElementById("valFaces");

  if (valFps) valFps.innerText = isPipelineRunning ? (state.fps || 0.0) + " FPS" : "--";
  if (valLatency) valLatency.innerText = isPipelineRunning ? (state.latency_ms || 0.0) + " ms" : "--";
  if (valFrames) valFrames.innerText = state.frames_processed || 0;
  if (valFaces) valFaces.innerText = isPipelineRunning ? (state.faces_detected || 0) : "--";

  // Monitor footer info
  const monitorInfo = document.getElementById("monitorInfo");
  if (monitorInfo) {
    monitorInfo.innerText = isPipelineRunning 
      ? `STREAMING: ${state.input_source.toUpperCase()} (${currentFile}) | TARGET: ${state.device} | POLICY: ${state.policy}` 
      : "STANDBY • PIPELINE PAUSED";
  }

  // Update Dedicated Analytics Dashboards (Red Zone & Blue Tripwire)
  const an = state.analytics || {};
  
  // 1. Red Zone Security Card
  const zoneStatusBadge = document.getElementById("zoneStatusBadge");
  const zoneSignalBox = document.getElementById("zoneSignalBox");
  const zoneSignalTitle = document.getElementById("zoneSignalTitle");
  const zoneSignalDesc = document.getElementById("zoneSignalDesc");
  const zoneIntrusionsVal = document.getElementById("zoneIntrusionsVal");
  const zoneOccupantsVal = document.getElementById("zoneOccupantsVal");
  const zoneCountMetricVal = document.getElementById("zoneCountMetricVal");
  const zonesCountLabel = document.getElementById("zonesCountLabel");
  const zoneLastAlertVal = document.getElementById("zoneLastAlertVal");

  if (zoneStatusBadge && zoneSignalBox) {
    const isZoneActive = an.zone_active !== false && currentZoneEnabled;
    const isIntruded = Boolean(an.zone_intruded_now);
    const occupants = an.current_zone_occupants || 0;

    if (!isZoneActive) {
      zoneStatusBadge.innerText = "STANDBY • DISABLED";
      zoneStatusBadge.className = "status-badge badge-zone";
      zoneSignalBox.classList.remove("zone-alert");
      zoneSignalTitle.innerText = "ZONE INACTIVE";
      zoneSignalDesc.innerText = "Restricted zone monitoring disabled in toolbar";
    } else if (isIntruded || occupants > 0) {
      zoneStatusBadge.innerText = "🚨 INTRUSION ACTIVE";
      zoneStatusBadge.className = "status-badge badge-zone alert-active";
      zoneSignalBox.classList.add("zone-alert");
      zoneSignalTitle.innerText = "RESTRICTED ZONE INTRUSION!";
      zoneSignalDesc.innerText = `${occupants} unauthorized subject(s) currently detected inside perimeter boundary`;
    } else {
      zoneStatusBadge.innerText = "ARMED • SECURE";
      zoneStatusBadge.className = "status-badge badge-zone";
      zoneSignalBox.classList.remove("zone-alert");
      zoneSignalTitle.innerText = "PERIMETER CLEAR";
      zoneSignalDesc.innerText = "No unauthorized intrusions detected in designated zones";
    }

    if (zoneOccupantsVal) {
      zoneOccupantsVal.innerText = occupants;
      if (occupants > 0) {
        zoneOccupantsVal.classList.add("pulse-red");
      } else {
        zoneOccupantsVal.classList.remove("pulse-red");
      }
    }
    if (zoneIntrusionsVal) zoneIntrusionsVal.innerText = an.total_intrusions || 0;
    if (zoneLastAlertVal) zoneLastAlertVal.innerText = an.last_intrusion_time || "None";
    
    const zCount = an.zones_count || 1;
    const zText = zCount + (zCount === 1 ? " Zone" : " Zones");
    if (zoneCountMetricVal) zoneCountMetricVal.innerText = zText;
    if (zonesCountLabel) zonesCountLabel.innerText = zText;

    renderZonesCardsGrid(an.zones_detailed || []);
    renderZoneFeed(an.zone_history || []);
  }

  // 2. Blue / Cyan Virtual Tripwire Card
  const tripwireStatusBadge = document.getElementById("tripwireStatusBadge");
  const tripwireSignalBox = document.getElementById("tripwireSignalBox");
  const tripwireSignalTitle = document.getElementById("tripwireSignalTitle");
  const tripwireSignalDesc = document.getElementById("tripwireSignalDesc");
  const twEntriesVal = document.getElementById("twEntriesVal");
  const twExitsVal = document.getElementById("twExitsVal");
  const twOccupancyVal = document.getElementById("twOccupancyVal");
  const twLastCrossingVal = document.getElementById("twLastCrossingVal");

  if (tripwireStatusBadge && tripwireSignalBox) {
    const isTwActive = an.tripwire_active !== false && currentTripwireEnabled;

    if (!isTwActive) {
      tripwireStatusBadge.innerText = "STANDBY • DISABLED";
      tripwireStatusBadge.className = "status-badge badge-tripwire";
      tripwireSignalBox.classList.remove("signal-active");
      tripwireSignalTitle.innerText = "TRIPWIRE INACTIVE";
      tripwireSignalDesc.innerText = "Virtual tripwire flow counting disabled in toolbar";
    } else if (an.last_crossing_direction) {
      tripwireStatusBadge.innerText = `FLOW • ${an.last_crossing_direction}`;
      tripwireStatusBadge.className = "status-badge badge-tripwire pulse-cyan";
      tripwireSignalBox.classList.add("signal-active");
      tripwireSignalTitle.innerText = `FLOW EVENT: ${an.last_crossing_direction}`;
      tripwireSignalDesc.innerText = `Subject crossed boundary line (${an.last_crossing_direction} at ${an.last_crossing_time || 'now'})`;
    } else {
      tripwireStatusBadge.innerText = "ACTIVE • COUNTING";
      tripwireStatusBadge.className = "status-badge badge-tripwire";
      tripwireSignalBox.classList.remove("signal-active");
      tripwireSignalTitle.innerText = "FLOW NORMAL";
      tripwireSignalDesc.innerText = "Monitoring bidirectional boundary transitions";
    }

    if (twEntriesVal) twEntriesVal.innerText = an.entries || 0;
    if (twExitsVal) twExitsVal.innerText = an.exits || 0;
    if (twOccupancyVal) twOccupancyVal.innerText = an.occupancy || 0;
    if (twLastCrossingVal) {
      twLastCrossingVal.innerText = an.last_crossing_time ? `${an.last_crossing_direction} at ${an.last_crossing_time}` : "None";
    }

    updateTripwireDynamics(an.tripwire_detailed);
    renderTripwireFeed(an.tripwire_history || []);
  }

  // 3. Stage B: Update Crowd & Occupancy Widget and Cryptography Panel
  updateCrowdWidget(state);
  updateCryptoPanel(state);
}

let lastLogCount = 0;
function renderLogs(logs) {
  const terminal = document.getElementById("logTerminal");
  if (!terminal) return;

  if (logs.length === lastLogCount) return;
  lastLogCount = logs.length;

  terminal.innerHTML = "";
  logs.forEach(item => {
    const row = document.createElement("div");
    row.className = "log-entry";
    row.innerHTML = `
      <span class="log-time">[${item.time}]</span>
      <span class="log-level ${item.level}">${item.level}:</span>
      <span class="log-msg">${escapeHtml(item.message)}</span>
    `;
    terminal.appendChild(row);
  });

  terminal.scrollTop = terminal.scrollHeight;
}

function escapeHtml(str) {
  return str.replace(/[&<>"']/g, m => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[m]));
}

const ZONE_CARD_THEMES = [
  { icon: "🛡️", iconClass: "icon-pink", pctClass: "pct-pink", fillClass: "fill-pink", arrowClass: "arrow-pink" },
  { icon: "🏪", iconClass: "icon-blue", pctClass: "pct-blue", fillClass: "fill-blue", arrowClass: "arrow-blue" },
  { icon: "⚠️", iconClass: "icon-yellow", pctClass: "pct-yellow", fillClass: "fill-yellow", arrowClass: "arrow-yellow" },
  { icon: "🚪", iconClass: "icon-green", pctClass: "pct-green", fillClass: "fill-green", arrowClass: "arrow-green" }
];

function renderZonesCardsGrid(zones) {
  const container = document.getElementById("zonesCardsGrid");
  if (!container) return;

  if (!zones || zones.length === 0) {
    container.innerHTML = '<div class="feed-empty">No active restricted zones configured. Use "➕ Add Zone" in toolbar.</div>';
    return;
  }

  container.innerHTML = zones.map((z, idx) => {
    const theme = ZONE_CARD_THEMES[idx % ZONE_CARD_THEMES.length];
    const isIntruded = z.occupants > 0;
    const isLoiter = z.status === "LOITERING";
    const statusClass = isLoiter ? "status-loitering" : (isIntruded ? "status-intruded" : "");
    const badgeClass = isLoiter ? "badge-loitering" : (isIntruded ? "badge-intruded" : "badge-secure");
    const badgeText = isLoiter ? "⚠️ LOITERING" : (isIntruded ? "🚨 INTRUDED" : "✓ SECURE");

    return `
      <div class="zone-venue-card ${statusClass}">
        <div class="zone-venue-top">
          <div class="zone-venue-icon-wrap ${theme.iconClass}">${theme.icon}</div>
          <span class="zone-venue-badge ${badgeClass}">${badgeText}</span>
        </div>
        <div class="zone-venue-name">${escapeHtml(z.name || `Zone #${idx + 1}`)}</div>
        <div class="zone-venue-stat-row">
          <div class="zone-venue-pct ${theme.pctClass}">${z.occupancy_pct}%</div>
          <div class="zone-venue-occ-label">Occupied (${z.occupants}/${z.capacity})</div>
        </div>
        <div class="zone-venue-bar">
          <div class="zone-venue-fill ${theme.fillClass}" style="width: ${Math.max(4, z.occupancy_pct)}%"></div>
        </div>
        <div class="zone-venue-footer">
          <span class="zone-venue-dwell">Avg Dwell: ${z.avg_dwell_sec}s &bull; Peak: ${z.peak_occupants}</span>
          <div class="zone-venue-arrow ${theme.arrowClass}" title="Zone #${idx + 1}">➔</div>
        </div>
      </div>
    `;
  }).join("");
}

function updateTripwireDynamics(td) {
  if (!td) return;
  const twFlowRateVal = document.getElementById("twFlowRateVal");
  const twPeakVelocityVal = document.getElementById("twPeakVelocityVal");
  const twNetPaceVal = document.getElementById("twNetPaceVal");
  const twInflowBar = document.getElementById("twInflowBar");
  const twOutflowBar = document.getElementById("twOutflowBar");
  const twInflowPctVal = document.getElementById("twInflowPctVal");
  const twOutflowPctVal = document.getElementById("twOutflowPctVal");
  const twFlowVelocityBadge = document.getElementById("twFlowVelocityBadge");

  if (twFlowRateVal) {
    twFlowRateVal.innerHTML = `${td.flow_rate_per_min || 0} <span class="flow-dyn-unit">crossings / min</span>`;
  }
  if (twPeakVelocityVal) {
    twPeakVelocityVal.innerText = `${td.peak_flow_rate || 0} / min`;
  }
  if (twNetPaceVal) {
    twNetPaceVal.innerText = td.net_flow_pace || "+0 / min";
  }
  if (twInflowBar && twOutflowBar) {
    twInflowBar.style.width = `${td.inflow_pct || 50}%`;
    twOutflowBar.style.width = `${td.outflow_pct || 50}%`;
  }
  if (twInflowPctVal) twInflowPctVal.innerText = `${td.inflow_pct || 50}%`;
  if (twOutflowPctVal) twOutflowPctVal.innerText = `${td.outflow_pct || 50}%`;

  if (twFlowVelocityBadge) {
    const rate = td.flow_rate_per_min || 0;
    if (rate >= 10) {
      twFlowVelocityBadge.innerText = "HIGH INFLUX";
      twFlowVelocityBadge.style.color = "#EF4444";
      twFlowVelocityBadge.style.background = "rgba(239, 68, 68, 0.2)";
    } else if (rate >= 4) {
      twFlowVelocityBadge.innerText = "ACTIVE FLOW";
      twFlowVelocityBadge.style.color = "#F59E0B";
      twFlowVelocityBadge.style.background = "rgba(245, 158, 11, 0.2)";
    } else {
      twFlowVelocityBadge.innerText = "NORMAL PACE";
      twFlowVelocityBadge.style.color = "#38BDF8";
      twFlowVelocityBadge.style.background = "rgba(56, 189, 248, 0.12)";
    }
  }
}

function renderZoneFeed(history) {
  const container = document.getElementById("zoneFeedList");
  if (!container) return;
  if (!history || history.length === 0) {
    container.innerHTML = '<div class="feed-empty">No intrusion incidents logged yet. Perimeter secure.</div>';
    return;
  }
  container.innerHTML = history.slice(0, 10).map(item => `
    <div class="feed-item">
      <span class="feed-time">[${item.time}]</span>
      <span class="feed-badge badge-entry-red">ENTRY</span>
      <span class="feed-text">${escapeHtml(item.message || "Unauthorized subject entered zone")}</span>
    </div>
  `).join("");
}

function renderTripwireFeed(history) {
  const container = document.getElementById("tripwireFeedList");
  if (!container) return;
  if (!history || history.length === 0) {
    container.innerHTML = '<div class="feed-empty">No line crossings detected yet. Monitoring boundary.</div>';
    return;
  }
  container.innerHTML = history.slice(0, 10).map(item => `
    <div class="feed-item">
      <span class="feed-time">[${item.time}]</span>
      <span class="feed-badge ${item.direction === 'ENTRY' ? 'badge-in' : 'badge-out'}">${item.direction || 'CROSS'}</span>
      <span class="feed-text">${escapeHtml(item.message || "Boundary crossed")}</span>
    </div>
  `).join("");
}

// -------------------------------------------------------------
// Interactive Analytics Zones & Virtual Tripwires Controller
// -------------------------------------------------------------
let currentZoneEnabled = true;
let currentTripwireEnabled = true;
let activeDrawMode = null; // null | "zone" | "add_zone" | "tripwire"
let isDrawing = false;
let drawStartX = 0;
let drawStartY = 0;

function initAnalyticsDrawingController() {
  const btnToggleZone = document.getElementById("btnToggleZone");
  const btnToggleTripwire = document.getElementById("btnToggleTripwire");
  const btnDrawZone = document.getElementById("btnDrawZone");
  const btnAddZone = document.getElementById("btnAddZone");
  const btnDrawTripwire = document.getElementById("btnDrawTripwire");
  const btnResetAnalytics = document.getElementById("btnResetAnalytics");
  const zonesCountLabel = document.getElementById("zonesCountLabel");
  const cancelDrawBtn = document.getElementById("cancelDrawBtn");
  const canvas = document.getElementById("interactiveCanvas");
  const banner = document.getElementById("drawBanner");
  const bannerText = document.getElementById("drawBannerText");
  const bannerIcon = document.getElementById("drawBannerIcon");
  const monitorImg = document.getElementById("monitorFeed");

  if (!canvas || !monitorImg) return;
  const ctx = canvas.getContext("2d");

  function resizeCanvas() {
    if (monitorImg.clientWidth > 0 && monitorImg.clientHeight > 0) {
      canvas.width = monitorImg.clientWidth;
      canvas.height = monitorImg.clientHeight;
    }
  }

  window.addEventListener("resize", resizeCanvas);
  monitorImg.addEventListener("load", resizeCanvas);
  setTimeout(resizeCanvas, 400);

  // Fetch initial enabled states
  fetchAnalyticsConfig();

  function fetchAnalyticsConfig() {
    fetch("/api/analytics/config")
      .then(res => res.json())
      .then(data => {
        if (data.status === "ok") {
          currentZoneEnabled = Boolean(data.zone_enabled);
          currentTripwireEnabled = Boolean(data.tripwire_enabled);
          updateAnalyticsButtons();
          const zCount = data.zones_count || 1;
          const zText = zCount + (zCount === 1 ? " Zone" : " Zones");
          if (zonesCountLabel) zonesCountLabel.innerText = zText;
          const zMetric = document.getElementById("zoneCountMetricVal");
          if (zMetric) zMetric.innerText = zText;
        }
      })
      .catch(err => console.error("Error fetching analytics config:", err));
  }

  function updateAnalyticsButtons() {
    if (btnToggleZone) {
      btnToggleZone.innerText = currentZoneEnabled ? "Zone: ON" : "Zone: OFF";
      btnToggleZone.classList.toggle("active", currentZoneEnabled);
    }
    if (btnToggleTripwire) {
      btnToggleTripwire.innerText = currentTripwireEnabled ? "Tripwire: ON" : "Tripwire: OFF";
      btnToggleTripwire.classList.toggle("active", currentTripwireEnabled);
    }
  }

  // Toggle Zone button
  if (btnToggleZone) {
    btnToggleZone.addEventListener("click", () => {
      const nextState = !currentZoneEnabled;
      fetch("/api/analytics/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zone_enabled: nextState })
      })
        .then(res => res.json())
        .then(data => {
          currentZoneEnabled = Boolean(data.zone_enabled);
          updateAnalyticsButtons();
        });
    });
  }

  // Toggle Tripwire button
  if (btnToggleTripwire) {
    btnToggleTripwire.addEventListener("click", () => {
      const nextState = !currentTripwireEnabled;
      fetch("/api/analytics/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tripwire_enabled: nextState })
      })
        .then(res => res.json())
        .then(data => {
          currentTripwireEnabled = Boolean(data.tripwire_enabled);
          updateAnalyticsButtons();
        });
    });
  }

  // Reset defaults button
  if (btnResetAnalytics) {
    btnResetAnalytics.addEventListener("click", () => {
      fetch("/api/analytics/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reset_defaults: true })
      })
        .then(res => res.json())
        .then(() => {
          fetchAnalyticsConfig();
        });
    });
  }

  // Edit Primary Zone
  if (btnDrawZone) {
    btnDrawZone.addEventListener("click", () => {
      startDrawingMode("zone");
    });
  }

  // Add Additional Zone
  if (btnAddZone) {
    btnAddZone.addEventListener("click", () => {
      startDrawingMode("add_zone");
    });
  }

  // Start Drawing Tripwire
  if (btnDrawTripwire) {
    btnDrawTripwire.addEventListener("click", () => {
      startDrawingMode("tripwire");
    });
  }

  if (cancelDrawBtn) {
    cancelDrawBtn.addEventListener("click", cancelDrawingMode);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && activeDrawMode) {
      cancelDrawingMode();
    }
  });

  function startDrawingMode(mode) {
    resizeCanvas();
    activeDrawMode = mode;
    canvas.classList.add("active");
    if (banner) {
      banner.style.display = "flex";
      if (mode === "zone") {
        bannerIcon.innerText = "✏️";
        bannerText.innerText = "Click & drag a rectangle on the video to configure Primary Restricted Zone";
      } else if (mode === "add_zone") {
        bannerIcon.innerText = "➕";
        bannerText.innerText = "Click & drag a rectangle on the video to add an Additional Restricted Zone";
      } else {
        bannerIcon.innerText = "📏";
        bannerText.innerText = "Click & drag across the video to place Virtual Tripwire crossing line";
      }
    }
  }

  function cancelDrawingMode() {
    activeDrawMode = null;
    isDrawing = false;
    canvas.classList.remove("active");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (banner) banner.style.display = "none";
  }

  let drawStartClientX = 0;
  let drawStartClientY = 0;

  function mapClickToVideoFrame(clientX, clientY) {
    const rect = canvas.getBoundingClientRect();
    const clickX = clientX - rect.left;
    const clickY = clientY - rect.top;

    const natW = monitorImg.naturalWidth > 0 ? monitorImg.naturalWidth : 1280;
    const natH = monitorImg.naturalHeight > 0 ? monitorImg.naturalHeight : 720;

    const elemW = canvas.width;
    const elemH = canvas.height;

    const natAspect = natW / natH;
    const elemAspect = elemW / elemH;

    let renderW, renderH, offsetX, offsetY;
    if (elemAspect > natAspect) {
      // Narrow / Vertical video with pillarboxes on left and right
      renderH = elemH;
      renderW = elemH * natAspect;
      offsetX = (elemW - renderW) / 2;
      offsetY = 0;
    } else {
      // Wide / Horizontal video with letterboxes on top and bottom
      renderW = elemW;
      renderH = elemW / natAspect;
      offsetX = 0;
      offsetY = (elemH - renderH) / 2;
    }

    const relX = clickX - offsetX;
    const relY = clickY - offsetY;

    const frameX = Math.round(Math.max(0, Math.min(natW, relX * (natW / renderW))));
    const frameY = Math.round(Math.max(0, Math.min(natH, relY * (natH / renderH))));

    return { frameX, frameY };
  }

  // Mouse event listeners for interactive drawing
  canvas.addEventListener("mousedown", (e) => {
    if (!activeDrawMode) return;
    const rect = canvas.getBoundingClientRect();
    drawStartX = e.clientX - rect.left;
    drawStartY = e.clientY - rect.top;
    drawStartClientX = e.clientX;
    drawStartClientY = e.clientY;
    isDrawing = true;
  });

  canvas.addEventListener("mousemove", (e) => {
    if (!activeDrawMode || !isDrawing) return;
    const rect = canvas.getBoundingClientRect();
    const currX = e.clientX - rect.left;
    const currY = e.clientY - rect.top;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    if (activeDrawMode === "zone" || activeDrawMode === "add_zone") {
      const rx = Math.min(drawStartX, currX);
      const ry = Math.min(drawStartY, currY);
      const rw = Math.abs(currX - drawStartX);
      const rh = Math.abs(currY - drawStartY);

      ctx.fillStyle = "rgba(239, 68, 68, 0.28)";
      ctx.fillRect(rx, ry, rw, rh);
      ctx.strokeStyle = "#EF4444";
      ctx.lineWidth = 2;
      ctx.setLineDash([6, 4]);
      ctx.strokeRect(rx, ry, rw, rh);
      ctx.setLineDash([]);

      ctx.fillStyle = "#18181B";
      ctx.fillRect(rx, Math.max(0, ry - 24), 180, 22);
      ctx.fillStyle = "#EF4444";
      ctx.font = "bold 11px sans-serif";
      const labelText = activeDrawMode === "add_zone" 
        ? `+ NEW ZONE: ${Math.round(rw)}x${Math.round(rh)}px` 
        : `PRIMARY ZONE: ${Math.round(rw)}x${Math.round(rh)}px`;
      ctx.fillText(labelText, rx + 6, Math.max(14, ry - 8));
    } else if (activeDrawMode === "tripwire") {
      ctx.strokeStyle = "#38BDF8";
      ctx.lineWidth = 3;
      ctx.beginPath();
      ctx.moveTo(drawStartX, drawStartY);
      ctx.lineTo(currX, currY);
      ctx.stroke();

      ctx.fillStyle = "#38BDF8";
      ctx.beginPath();
      ctx.arc(drawStartX, drawStartY, 5, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.arc(currX, currY, 5, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = "#18181B";
      ctx.fillRect(drawStartX, Math.max(0, drawStartY - 24), 180, 22);
      ctx.fillStyle = "#38BDF8";
      ctx.font = "bold 11px sans-serif";
      ctx.fillText("TRIPWIRE (ENTRY / EXIT)", drawStartX + 6, Math.max(14, drawStartY - 8));
    }
  });

  canvas.addEventListener("mouseup", (e) => {
    if (!activeDrawMode || !isDrawing) return;

    const startPt = mapClickToVideoFrame(drawStartClientX, drawStartClientY);
    const endPt = mapClickToVideoFrame(e.clientX, e.clientY);

    if (activeDrawMode === "zone" || activeDrawMode === "add_zone") {
      const rx1 = Math.min(startPt.frameX, endPt.frameX);
      const ry1 = Math.min(startPt.frameY, endPt.frameY);
      const rx2 = Math.max(startPt.frameX, endPt.frameX);
      const ry2 = Math.max(startPt.frameY, endPt.frameY);

      if (Math.abs(rx2 - rx1) > 15 && Math.abs(ry2 - ry1) > 15) {
        const poly = [
          [rx1, ry1],
          [rx2, ry1],
          [rx2, ry2],
          [rx1, ry2]
        ];
        const payload = activeDrawMode === "add_zone"
          ? { add_zone: true, zone_polygon: poly, zone_enabled: true }
          : { zone_polygon: poly, zone_enabled: true };

        fetch("/api/analytics/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        })
          .then(res => res.json())
          .then(data => {
            currentZoneEnabled = true;
            updateAnalyticsButtons();
            fetchAnalyticsConfig();
          });
      }
    } else if (activeDrawMode === "tripwire") {
      const p1x = startPt.frameX;
      const p1y = startPt.frameY;
      const p2x = endPt.frameX;
      const p2y = endPt.frameY;

      if (Math.hypot(p2x - p1x, p2y - p1y) > 15) {
        fetch("/api/analytics/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            tripwire_p1: [p1x, p1y],
            tripwire_p2: [p2x, p2y],
            tripwire_enabled: true
          })
        })
          .then(res => res.json())
          .then(data => {
            currentTripwireEnabled = true;
            updateAnalyticsButtons();
          });
      }
    }

    cancelDrawingMode();
  });
}

/* ==========================================================================
   STAGE B: CROWD WIDGET, LIVE JSON STREAM VIEWER & CRYPTO INSPECTOR
   ========================================================================== */

function initCrowdWidget() {
  // Crowd widget initialized
}

function updateCrowdWidget(state) {
  if (!state) return;
  const count = (typeof state.crowd_count === "number") ? state.crowd_count : (state.faces_detected || 0);
  const crowdVal = document.getElementById("crowdHeadcountVal");
  if (crowdVal) crowdVal.innerText = count;

  // Scene Capacity Utilization (safe venue baseline threshold: 15)
  const capPct = Math.min(100, Math.round((count / 15) * 100));
  const capPctVal = document.getElementById("crowdCapacityPctVal");
  const progFill = document.getElementById("crowdProgressFill");
  if (capPctVal) capPctVal.innerText = `${capPct}%`;
  if (progFill) {
    progFill.style.width = `${capPct}%`;
    progFill.classList.toggle("fill-danger", capPct >= 80);
  }

  // Density tier badge & metrics
  const tierBadge = document.getElementById("crowdDensityTierBadge");
  const levelText = document.getElementById("crowdLevelText");
  const netFlowVal = document.getElementById("crowdNetFlowVal");
  const riskVal = document.getElementById("crowdRiskVal");

  const level = (state.crowd_level || "LOW").toUpperCase();
  if (levelText) levelText.innerText = level;
  if (netFlowVal) netFlowVal.innerText = `${state.occupancy || 0} Occ`;

  if (tierBadge) {
    if (level === "HIGH" || count >= 10) {
      tierBadge.innerText = "🚨 HIGH CONGESTION";
      tierBadge.className = "status-badge badge-crowd-tier tier-high";
      if (riskVal) {
        riskVal.innerText = "CONGESTION ALERT";
        riskVal.style.color = "#EF4444";
      }
    } else if (level === "NORMAL" || count >= 4) {
      tierBadge.innerText = "MODERATE DENSITY";
      tierBadge.className = "status-badge badge-crowd-tier tier-warn";
      if (riskVal) {
        riskVal.innerText = "ELEVATED";
        riskVal.style.color = "#F59E0B";
      }
    } else {
      tierBadge.innerText = "LOW DENSITY";
      tierBadge.className = "status-badge badge-crowd-tier";
      if (riskVal) {
        riskVal.innerText = "SAFE";
        riskVal.style.color = "#10B981";
      }
    }
  }
}

let isSessionKeySelected = false;
let currentSelectedRoiId = "latest";
let currentSelectedTrackId = "latest";
let isUnblurredStreamActive = false;

function getSelectedFaceDisplayName() {
  if (currentSelectedTrackId === "all" || currentSelectedRoiId === "all") {
    return "All Faces";
  } else if (currentSelectedTrackId && currentSelectedTrackId !== "latest") {
    return `Person #${currentSelectedTrackId}`;
  } else {
    return "Selected Face";
  }
}

function updateButtonLabelsForSelectedFace() {
  const targetName = getSelectedFaceDisplayName();
  const btnDecrypt = document.getElementById("btnDecryptTrigger");
  if (btnDecrypt) {
    btnDecrypt.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-1-1l-3 3m5 8v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/></svg>
      🔓 Decrypt &amp; Open Face Image (${targetName})
    `;
  }
  const btnToggleUnblurStream = document.getElementById("btnToggleUnblurStream");
  if (btnToggleUnblurStream && !btnToggleUnblurStream.disabled) {
    btnToggleUnblurStream.innerText = isUnblurredStreamActive
      ? `👁️ Unblur Video (${targetName}): ACTIVE (ON)`
      : `👁️ Unblur Video for ${targetName}: OFF (Click to Unblur)`;
  }
}

function initCryptoInspector() {
  fetchCryptoStatus();

  // Eye toggle for operator key input
  const btnToggleKeyVis = document.getElementById("btnToggleKeyVis");
  const keyInput = document.getElementById("operatorKeyInput");
  if (btnToggleKeyVis && keyInput) {
    btnToggleKeyVis.addEventListener("click", () => {
      keyInput.type = keyInput.type === "password" ? "text" : "password";
      btnToggleKeyVis.innerText = keyInput.type === "password" ? "👁️" : "🙈";
    });
  }

  // Use session key button
  const btnUseSessionKey = document.getElementById("btnUseSessionKey");
  if (btnUseSessionKey && keyInput) {
    btnUseSessionKey.addEventListener("click", () => {
      isSessionKeySelected = true;
      keyInput.value = "SESSION_MASTER_KEY_LOADED";
      keyInput.dataset.useMaster = "true";
      keyInput.style.borderColor = "#10B981";
    });
  }

  // Test tampered key button (demonstrates safe rejection)
  const btnTestTamperKey = document.getElementById("btnTestTamperKey");
  if (btnTestTamperKey && keyInput) {
    btnTestTamperKey.addEventListener("click", () => {
      isSessionKeySelected = false;
      keyInput.dataset.useMaster = "false";
      keyInput.value = "deadbeef00000000000000000000000000000000000000000000000000000000";
      keyInput.style.borderColor = "#EF4444";
    });
  }

  if (keyInput) {
    keyInput.addEventListener("input", () => {
      isSessionKeySelected = false;
      keyInput.dataset.useMaster = "false";
      keyInput.style.borderColor = "";
    });
  }

  // Decrypt trigger button (previews face image snapshot ONLY; does NOT unblur video stream)
  const btnDecryptTrigger = document.getElementById("btnDecryptTrigger");
  if (btnDecryptTrigger) {
    btnDecryptTrigger.addEventListener("click", performAuthorizedDecryption);
  }

  // Lock session button
  const btnLockCrypto = document.getElementById("btnLockCrypto");
  if (btnLockCrypto) {
    btnLockCrypto.addEventListener("click", lockCryptoSession);
  }

  // Unblurred live stream toggle buttons (in Inspector and in Toolbar)
  const btnToggleUnblurStream = document.getElementById("btnToggleUnblurStream");
  if (btnToggleUnblurStream) {
    btnToggleUnblurStream.addEventListener("click", toggleLiveUnblurredStream);
  }

  const btnToolbarUnblurToggle = document.getElementById("btnToolbarUnblurToggle");
  if (btnToolbarUnblurToggle) {
    btnToolbarUnblurToggle.addEventListener("click", toggleLiveUnblurredStream);
  }

  // Dropdown change
  const roiDropdown = document.getElementById("roiSelectorDropdown");
  if (roiDropdown) {
    roiDropdown.addEventListener("change", (e) => {
      currentSelectedRoiId = e.target.value;
      const opt = roiDropdown.selectedOptions[0];
      currentSelectedTrackId = opt ? (opt.dataset.track || (e.target.value === "all" ? "all" : "latest")) : "latest";
      updateFacePillsActiveState();
    });
  }
}

function updateFacePillsActiveState() {
  const pills = document.querySelectorAll("#facePillsRow .face-pill");
  pills.forEach(p => {
    p.classList.toggle("active", p.dataset.roi === currentSelectedRoiId);
  });
  updateButtonLabelsForSelectedFace();
}

function fetchCryptoStatus() {
  fetch("/api/crypto/status")
    .then(r => r.json())
    .then(data => {
      if (data) {
        const fpEl = document.getElementById("cryptoKeyFp");
        if (fpEl && data.key_fingerprint) {
          fpEl.innerText = `SHA-256: ${data.key_fingerprint}`;
        }
      }
    })
    .catch(() => {});
}

function updateCryptoPanel(state) {
  if (!state) return;

  const cryptoEngineBadge = document.getElementById("cryptoEngineBadge");
  const cryptoCountVal = document.getElementById("cryptoCountVal");
  const cryptoPolicyBanner = document.getElementById("cryptoPolicyBanner");
  const cryptoPolicyTitle = document.getElementById("cryptoPolicyTitle");
  const cryptoPolicyDesc = document.getElementById("cryptoPolicyDesc");
  const roiDropdown = document.getElementById("roiSelectorDropdown");
  const facePillsRow = document.getElementById("facePillsRow");
  const countBadge = document.getElementById("multiFaceCountBadge");

  const isEncActive = Boolean(state.encryption_active);
  if (cryptoEngineBadge) {
    if (isEncActive) {
      cryptoEngineBadge.innerText = "AES-256-GCM ACTIVE";
      cryptoEngineBadge.className = "status-badge badge-crypto active-encrypting";
    } else {
      cryptoEngineBadge.innerText = "AES-256-GCM READY";
      cryptoEngineBadge.className = "status-badge badge-crypto";
    }
  }

  if (cryptoCountVal) {
    cryptoCountVal.innerText = `${state.encrypted_faces_count || 0} Faces`;
  }

  if (cryptoPolicyBanner && cryptoPolicyTitle && cryptoPolicyDesc) {
    if (state.policy === "ZERO_TRUST") {
      cryptoPolicyBanner.className = "crypto-policy-banner banner-zero-trust";
      cryptoPolicyTitle.innerText = "Active Policy: ZERO_TRUST (Reversible AES-256-GCM)";
      cryptoPolicyDesc.innerHTML = "Face ROIs are encrypted frame-by-frame with <strong>AES-256-GCM</strong> authenticated encryption. Full 256-bit entropy preservation enables authorized authenticated restoration.";
    } else {
      cryptoPolicyBanner.className = "crypto-policy-banner";
      cryptoPolicyTitle.innerText = `Active Policy: ${state.policy || "STANDARD"} (Irreversible Blur)`;
      cryptoPolicyDesc.innerHTML = "Standard Gaussian blur permanently destroys pixel entropy. Switch to <strong>ZERO_TRUST</strong> policy above to activate reversible AES-256-GCM face-ROI envelopes.";
    }
  }

  // Sync unblurred stream UI status
  const isUnlocked = Boolean(state.authorized_decryption_unlocked);
  isUnblurredStreamActive = Boolean(state.view_unblurred_video);
  updateUnblurredStreamUI(isUnlocked, isUnblurredStreamActive);

  // Multi-person face tracks rebuild
  const rois = state.recent_encrypted_rois || [];
  const uniqueTracks = {};
  rois.forEach(r => {
    const tid = r.track_id || 1;
    uniqueTracks[tid] = r;
  });
  const trackIds = Object.keys(uniqueTracks);

  if (countBadge) {
    countBadge.innerText = trackIds.length > 0 
      ? `${trackIds.length} Subject${trackIds.length > 1 ? "s" : ""} in Buffer` 
      : "0 Subjects";
  }

  // Populate Face Pills Row
  if (facePillsRow) {
    const existingPills = facePillsRow.querySelectorAll(".face-pill");
    const expectedCount = trackIds.length + 2;
    if (existingPills.length !== expectedCount) {
      let pillsHtml = `
        <button type="button" class="face-pill ${currentSelectedRoiId === 'latest' ? 'active' : ''}" data-roi="latest" data-track="latest">Latest Face</button>
        <button type="button" class="face-pill ${currentSelectedRoiId === 'all' ? 'active' : ''}" data-roi="all" data-track="all">👥 All Faces</button>
      `;
      trackIds.forEach(tid => {
        const r = uniqueTracks[tid];
        const isActive = (currentSelectedRoiId === r.roi_id || currentSelectedTrackId === String(tid));
        pillsHtml += `<button type="button" class="face-pill ${isActive ? 'active' : ''}" data-roi="${r.roi_id}" data-track="${tid}">👤 Person #${tid}</button>`;
      });
      facePillsRow.innerHTML = pillsHtml;

      facePillsRow.querySelectorAll(".face-pill").forEach(pill => {
        pill.addEventListener("click", () => {
          currentSelectedRoiId = pill.dataset.roi;
          currentSelectedTrackId = pill.dataset.track || "latest";
          if (roiDropdown) roiDropdown.value = currentSelectedRoiId;
          updateFacePillsActiveState();
        });
      });
    } else {
      updateFacePillsActiveState();
    }
  }

  // Populate Dropdown
  if (roiDropdown && rois.length > 0) {
    const expectedOptions = trackIds.length + 2;
    if (roiDropdown.options.length !== expectedOptions) {
      let optionsHtml = `
        <option value="latest" data-track="latest">Latest Captured Face-ROI Envelope</option>
        <option value="all" data-track="all">👥 All Detected Faces</option>
      `;
      trackIds.forEach(tid => {
        const r = uniqueTracks[tid];
        optionsHtml += `<option value="${r.roi_id}" data-track="${tid}">Person #${tid} (ROI #${r.roi_id.substring(0, 8)} - Frame ${r.frame_index})</option>`;
      });
      roiDropdown.innerHTML = optionsHtml;
      if (currentSelectedRoiId && roiDropdown.querySelector(`option[value="${currentSelectedRoiId}"]`)) {
        roiDropdown.value = currentSelectedRoiId;
      } else {
        roiDropdown.value = "latest";
      }
    }
  }
}

function updateUnblurredStreamUI(isUnlocked, isUnblurred) {
  const btnToggleUnblurStream = document.getElementById("btnToggleUnblurStream");
  const badge = document.getElementById("streamUnblurBadge");
  const desc = document.getElementById("streamUnblurDesc");
  const btnToolbar = document.getElementById("btnToolbarUnblurToggle");
  const divider = document.getElementById("unblurDivider");
  const overlayPill = document.getElementById("liveUnblurOverlayPill");
  const targetName = getSelectedFaceDisplayName();

  if (isUnlocked) {
    if (btnToggleUnblurStream) {
      btnToggleUnblurStream.disabled = false;
      btnToggleUnblurStream.className = isUnblurred ? "btn-toggle-unblur active-unblur" : "btn-toggle-unblur enabled";
      btnToggleUnblurStream.innerText = isUnblurred 
        ? `👁️ Unblur Video (${targetName}): ACTIVE (ON)` 
        : `👁️ Unblur Video for ${targetName}: OFF (Click to Unblur)`;
    }
    if (badge) {
      badge.innerText = isUnblurred ? `UNBLURRED (${targetName.toUpperCase()})` : "KEY VERIFIED (STREAM MASKED)";
      badge.className = isUnblurred ? "status-badge badge-unblur-status unblur-active" : "status-badge badge-unblur-status";
    }
    if (desc) {
      desc.innerHTML = isUnblurred 
        ? `<strong style='color:#34D399'>Video stream is currently unmasked for ${targetName}.</strong> Other faces remain securely masked.`
        : `Master Key validated! Click above or in toolbar to unblur <strong>${targetName}</strong> live on the video monitor.`;
    }
    if (btnToolbar) {
      btnToolbar.style.display = "inline-flex";
      btnToolbar.innerText = isUnblurred ? `👁️ Clear: ON (${targetName})` : "👁️ Clear Feed: OFF";
      btnToolbar.style.background = isUnblurred ? "#059669" : "#1F2937";
      btnToolbar.style.borderColor = isUnblurred ? "#10B981" : "#374151";
      btnToolbar.style.color = isUnblurred ? "#FFFFFF" : "#9CA3AF";
    }
    if (divider) divider.style.display = "block";
    if (overlayPill) {
      overlayPill.style.display = isUnblurred ? "inline" : "none";
      overlayPill.innerText = `🔓 OPERATOR AUTHORIZED: UNBLURRED (${targetName.toUpperCase()})`;
    }

  } else {
    if (btnToggleUnblurStream) {
      btnToggleUnblurStream.disabled = true;
      btnToggleUnblurStream.className = "btn-toggle-unblur";
      btnToggleUnblurStream.innerText = "👁️ Unblur Video for Selected Face: LOCKED";
    }
    if (badge) {
      badge.innerText = "LOCKED (KEY REQUIRED)";
      badge.className = "status-badge badge-unblur-status";
    }
    if (desc) {
      desc.innerText = "Verify master key above first. Then click the button below to stream the unblurred video feed live on the monitor.";
    }
    if (btnToolbar) btnToolbar.style.display = "none";
    if (divider) divider.style.display = "none";
    if (overlayPill) overlayPill.style.display = "none";
  }
}

function toggleLiveUnblurredStream() {
  const target = currentSelectedTrackId || "all";
  fetch("/api/crypto/toggle_stream_unmask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target: target })
  })
    .then(r => r.json())
    .then(data => {
      if (data && data.status === "ok") {
        isUnblurredStreamActive = Boolean(data.view_unblurred_video);
        updateUnblurredStreamUI(true, isUnblurredStreamActive);
      } else {
        alert(data.message || "Failed to toggle unblurred video. Master key required.");
      }
    })
    .catch(err => console.error("Toggle unmask error:", err));
}

function performAuthorizedDecryption() {
  const roiDropdown = document.getElementById("roiSelectorDropdown");
  const keyInput = document.getElementById("operatorKeyInput");
  const previewBox = document.getElementById("decryptionPreviewBox");
  const errorBox = document.getElementById("decryptionErrorBox");
  const errorDesc = document.getElementById("decryptionErrorDesc");
  const faceImg = document.getElementById("decryptedFaceImg");

  const roiId = currentSelectedRoiId || (roiDropdown ? roiDropdown.value : "latest");
  const trackId = currentSelectedTrackId || "latest";
  const useMaster = keyInput && keyInput.dataset.useMaster === "true";
  const keyHex = keyInput ? keyInput.value.trim() : "";

  const payload = {
    roi_id: roiId,
    track_id: trackId,
    use_master_key: useMaster,
    key_hex: useMaster ? "" : keyHex,
    enable_unblurred_stream: false // NEVER auto-unblur video stream when decrypting face snapshot!
  };

  fetch("/api/crypto/decrypt", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
    .then(r => r.json().then(data => ({ status: r.status, ok: r.ok, data })))
    .then(res => {
      if (res.ok && res.data.status === "ok") {
        if (errorBox) errorBox.style.display = "none";

        // Session unlocked! Update unblur button state (video stream remains masked until operator clicks unblur)
        updateUnblurredStreamUI(true, Boolean(res.data.view_unblurred_video));

        if (previewBox) previewBox.style.display = "flex";

        if (res.data.decrypted_image_b64 && faceImg) {
          faceImg.src = res.data.decrypted_image_b64;
        }

        const roiMeta = document.getElementById("previewRoiMeta");
        const trackLabel = document.getElementById("previewSubjectTrack");
        const previewBBox = document.getElementById("previewBBox");
        const previewFrameIdx = document.getElementById("previewFrameIdx");
        const previewTime = document.getElementById("previewTime");

        if (roiMeta) roiMeta.innerText = `ROI #${(res.data.roi_id || "").substring(0, 8)}`;
        if (trackLabel) trackLabel.innerText = `Person #${res.data.track_id || 1}`;
        if (previewBBox) previewBBox.innerText = res.data.bbox ? `[${res.data.bbox.join(", ")}]` : "[--]";
        if (previewFrameIdx) previewFrameIdx.innerText = res.data.frame_index || "--";
        if (previewTime) previewTime.innerText = res.data.timestamp ? res.data.timestamp.replace("T", " ").replace("Z", "") : "--";

      } else {
        if (faceImg) faceImg.src = "";
        if (previewBox) previewBox.style.display = "none";
        if (errorBox) errorBox.style.display = "block";
        if (errorDesc) {
          errorDesc.innerText = res.data.message || "Decryption authentication failed: Invalid cryptographic key or tampered ciphertext.";
        }
      }
    })
    .catch(err => {
      if (faceImg) faceImg.src = "";
      if (previewBox) previewBox.style.display = "none";
      if (errorBox) errorBox.style.display = "block";
      if (errorDesc) errorDesc.innerText = `Decryption request failed: ${err.message}`;
    });
}

function lockCryptoSession() {
  fetch("/api/crypto/lock", { method: "POST" })
    .then(r => r.json())
    .then(() => {
      const previewBox = document.getElementById("decryptionPreviewBox");
      const errorBox = document.getElementById("decryptionErrorBox");
      const faceImg = document.getElementById("decryptedFaceImg");
      const keyInput = document.getElementById("operatorKeyInput");

      if (faceImg) faceImg.src = "";
      if (previewBox) previewBox.style.display = "none";
      if (errorBox) errorBox.style.display = "none";
      if (keyInput) {
        keyInput.value = "";
        keyInput.dataset.useMaster = "false";
        keyInput.style.borderColor = "";
      }
      isUnblurredStreamActive = false;
      updateUnblurredStreamUI(false, false);
    });
}

