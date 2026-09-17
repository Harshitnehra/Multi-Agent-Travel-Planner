let currentThreadId = null;
let currentTripId = null;
let latestAnswerMarkdown = "";
let authMode = "login";
let latestWorkspace = null;

const byId = (id) => document.getElementById(id);

function setPrompt(text) {
    byId("userInput").value = text;
    byId("userInput").focus();
}

function setBusy(isBusy, label = "Working...") {
    const sendButton = byId("sendBtn");
    sendButton.disabled = isBusy;
    byId("btnText").classList.toggle("hidden", isBusy);
    byId("btnLoader").classList.toggle("hidden", !isBusy);

    document.querySelectorAll(".approval-actions button").forEach((button) => {
        button.disabled = isBusy;
    });

    if (isBusy) {
        sendButton.setAttribute("aria-label", label);
    } else {
        sendButton.removeAttribute("aria-label");
    }
}

function showError(message) {
    const errorBox = byId("errorBox");
    errorBox.textContent = message;
    errorBox.classList.remove("hidden");
}

function hideError() {
    byId("errorBox").classList.add("hidden");
    byId("errorBox").textContent = "";
}

function apiErrorMessage(data, fallback) {
    if (typeof data?.error === "string") return data.error;
    if (typeof data?.detail === "string") return data.detail;
    if (Array.isArray(data?.detail)) {
        return data.detail.map((item) => item.msg || "Invalid value").join(" ");
    }
    return fallback;
}

function renderMarkdown(markdown) {
    const resultBox = byId("resultBox");
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
        resultBox.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
        return;
    }
    resultBox.textContent = markdown;
}

function stripStructuredPlanSection(markdown) {
    const text = String(markdown || "");
    const marker = text.search(
        /^\s{0,3}(?:#{1,6}\s*)?(?:\*{1,2}|_{1,2})?\s*structured\s+trip\s+plan\b.*$/im
    );
    let readable = marker < 0 ? text : text.slice(0, marker);
    readable = readable
        .replace(/```\s*json\s*[\s\S]*?```/gi, "")
        .replace(/\bsource_verified\b/gi, "Source checked")
        .replace(/\bnull\b/gi, "To be confirmed")
        .replace(/\|\s*true\s*\|/gi, "| Yes |")
        .replace(/\|\s*false\s*\|/gi, "| No |")
        .replace(
            /^\s*\*\*(trip summary|flights|hotels|day-by-day itinerary|budget|practical notes)\*\*\s*$/gim,
            "## $1"
        )
        .replace(/\n\s*(?:---|\*\*\*|___)\s*$/g, "")
        .trimEnd();
    return readable || "# Travel plan\n\nA readable itinerary is not available yet.";
}

function renderWorkflowResult(data) {
    currentThreadId = data.thread_id;
    currentTripId = data.trip_id || currentTripId;
    latestAnswerMarkdown = stripStructuredPlanSection(
        data.answer || data.itinerary || ""
    );
    localStorage.setItem("travel_thread_id", currentThreadId);

    renderMarkdown(latestAnswerMarkdown);
    byId("threadInfo").textContent = `Plan reference: ${currentThreadId}`;

    const awaitingReview = data.status === "approval_required";
    byId("approvalPanel").classList.toggle("hidden", !awaitingReview);
    byId("tripWorkspace").classList.add("hidden");
    byId("riskDecision").classList.add("hidden");
    byId("resultTitle").textContent = awaitingReview ? "Draft travel plan" : "Approved travel plan";

    if (!awaitingReview) {
        byId("revisionFeedback").value = "";
    }

    byId("resultSection").classList.remove("hidden");
    byId("resultSection").scrollIntoView({behavior: "smooth", block: "start"});
    if (!awaitingReview && currentTripId) loadWorkspace();
}

async function requestJson(url, payload = null, method = "POST") {
    let response;
    try {
        response = await fetch(url, {
            method,
            headers: payload === null ? {} : {"Content-Type": "application/json"},
            body: payload === null ? undefined : JSON.stringify(payload),
            credentials: "same-origin"
        });
    } catch {
        throw new Error("TripMate could not reach the server. Check that the app is running and try again.");
    }

    let data = {};
    try {
        data = await response.json();
    } catch {
        throw new Error(`The server returned an unreadable response (${response.status}).`);
    }
    if (!response.ok || !data.success) {
        throw new Error(apiErrorMessage(data, "The request could not be completed."));
    }
    return data;
}

async function sendMessage() {
    hideError();
    const message = byId("userInput").value.trim();
    if (!message) {
        showError("Enter a travel request first.");
        return;
    }

    setBusy(true, "Generating draft");
    byId("approvalPanel").classList.add("hidden");

    try {
        const data = await requestJson("/api/travel", {message, thread_id: null});
        renderWorkflowResult(data);
        await loadTrips();
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(false);
    }
}

async function submitApproval(action) {
    hideError();
    if (!currentThreadId) {
        showError("This draft has no plan reference. Generate a new plan.");
        return;
    }

    const feedback = byId("revisionFeedback").value.trim();
    if (action === "revise" && !feedback) {
        showError("Describe what you want changed before requesting a revision.");
        byId("revisionFeedback").focus();
        return;
    }

    setBusy(true, action === "revise" ? "Revising draft" : "Applying review");
    try {
        const data = await requestJson("/api/travel/approval", {
            thread_id: currentThreadId,
            action,
            feedback
        });
        renderWorkflowResult(data);
        await loadTrips();
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(false);
    }
}

function toggleAuthMode() {
    authMode = authMode === "login" ? "register" : "login";
    const registering = authMode === "register";
    byId("displayNameField").classList.toggle("hidden", !registering);
    byId("displayName").required = registering;
    byId("authTitle").textContent = registering ? "Create your account" : "Welcome back";
    byId("authHint").textContent = registering
        ? "Create a private workspace for your plans, reviews, and trip history."
        : "Sign in to continue planning and reopen your saved trips.";
    byId("authBtnText").textContent = registering ? "Create account" : "Sign in";
    byId("authSwitchPrompt").textContent = registering
        ? "Already have an account?"
        : "New to TripMate?";
    byId("authModeBtn").textContent = registering ? "Sign in" : "Create an account";
    byId("password").autocomplete = registering ? "new-password" : "current-password";
    byId("password").minLength = registering ? 10 : 1;
    byId("passwordHelp").classList.toggle("hidden", !registering);
    clearAuthMessage();
    hideError();
}

function setAuthBusy(isBusy) {
    byId("authBtn").disabled = isBusy;
    byId("authModeBtn").disabled = isBusy;
    byId("authBtnText").classList.toggle("hidden", isBusy);
    byId("authLoader").classList.toggle("hidden", !isBusy);
}

function clearAuthMessage() {
    const message = byId("authMessage");
    message.textContent = "";
    message.className = "form-message hidden";
}

function showAuthMessage(message) {
    const target = byId("authMessage");
    target.textContent = message;
    target.className = "form-message state-error";
}

async function submitAuth() {
    hideError();
    clearAuthMessage();
    const email = byId("email").value.trim();
    const password = byId("password").value;
    const payload = {email, password};
    if (!email || !byId("email").validity.valid) {
        showAuthMessage("Enter a valid email address.");
        byId("email").focus();
        return;
    }
    if (!password || (authMode === "register" && password.length < 10)) {
        showAuthMessage(authMode === "register"
            ? "Create a password with at least 10 characters."
            : "Enter your password.");
        byId("password").focus();
        return;
    }
    if (authMode === "register") {
        payload.display_name = byId("displayName").value.trim();
        if (!payload.display_name) {
            showAuthMessage("Enter your name to create an account.");
            byId("displayName").focus();
            return;
        }
    }

    setAuthBusy(true);
    try {
        const data = await requestJson(`/api/auth/${authMode}`, payload);
        showAuthenticated(data.user);
        byId("password").value = "";
        await loadTrips();
    } catch (error) {
        showAuthMessage(error.message);
    } finally {
        setAuthBusy(false);
    }
}

function showAuthenticated(user) {
    byId("authSection").classList.add("hidden");
    byId("accountBar").classList.remove("hidden");
    byId("plannerSection").classList.remove("hidden");
    byId("savedTripsSection").classList.remove("hidden");
    byId("accountName").textContent = user.display_name;
    byId("accountAvatar").textContent = (user.display_name || "T").trim().charAt(0).toUpperCase();
    clearAuthMessage();
}

function showSignedOut() {
    currentThreadId = null;
    currentTripId = null;
    byId("authSection").classList.remove("hidden");
    byId("accountBar").classList.add("hidden");
    byId("plannerSection").classList.add("hidden");
    byId("savedTripsSection").classList.add("hidden");
    byId("resultSection").classList.add("hidden");
}

async function logout() {
    hideError();
    try {
        await requestJson("/api/auth/logout", {});
        showSignedOut();
    } catch (error) {
        showError(error.message);
    }
}

async function loadTrips() {
    try {
        const data = await requestJson("/api/trips", null, "GET");
        const list = byId("savedTripsList");
        if (!data.trips.length) {
            list.innerHTML = '<p class="empty-state">No saved trips yet. Generate your first plan above.</p>';
            return;
        }
        list.innerHTML = data.trips.map((trip) => `
            <button class="trip-card" onclick="openTrip('${trip.id}')">
                <span><strong>${escapeHtml(trip.title)}</strong><small>${escapeHtml(trip.status)} · Version ${trip.current_version}</small></span>
                <span class="open-label">Open →</span>
            </button>
        `).join("");
    } catch (error) {
        if (error.message.toLowerCase().includes("sign in")) showSignedOut();
        else showError(error.message);
    }
}

async function openTrip(tripId) {
    hideError();
    try {
        const data = await requestJson(`/api/trips/${tripId}`, null, "GET");
        const trip = data.trip;
        currentTripId = trip.id;
        renderWorkflowResult({
            trip_id: trip.id,
            thread_id: trip.thread_id,
            status: trip.status === "paused" ? "approval_required" : trip.status,
            answer: trip.itinerary,
            itinerary: trip.itinerary
        });
    } catch (error) {
        showError(error.message);
    }
}

const workspaceLabels = {
    check_flight: "Check flight status",
    check_weather: "Check weather"
};

function applyWorkspace(workspace) {
    latestWorkspace = workspace;
    byId("tripWorkspace").classList.remove("hidden");
    byId("workspaceVersion").textContent = `Version ${workspace.version}`;
    Object.entries(workspace.actions).forEach(([action, capability]) => {
        const button = byId(`action-${action}`);
        if (!button || !workspaceLabels[action]) return;
        const canEnterFlightDetails = action === "check_flight" &&
            ["approved", "active"].includes(workspace.trip_status);
        const available = capability.enabled || canEnterFlightDetails;
        button.disabled = !available;
        button.dataset.available = available ? "true" : "false";
        button.title = capability.reason || "";
        button.className = available ? "" : "action-disabled";
        button.querySelector("span").textContent = workspaceLabels[action];
        button.querySelector("small").textContent = capability.enabled
            ? (action === "check_flight" ? "Ready for real lookup" : "Ready")
            : (canEnterFlightDetails ? "Enter flight number and date" : capability.reason);
    });
}

async function loadWorkspace() {
    if (!currentTripId) return;
    byId("workspaceNotice").textContent = "Loading workspace…";
    try {
        const data = await requestJson(`/api/trips/${currentTripId}/workspace`, null, "GET");
        applyWorkspace(data.workspace);
        byId("workspaceNotice").textContent = "Workspace ready.";
    } catch (error) {
        byId("tripWorkspace").classList.remove("hidden");
        Object.keys(workspaceLabels).forEach((action) => {
            const button = byId(`action-${action}`);
            button.disabled = true;
            button.dataset.available = "false";
            button.className = "state-error";
            button.querySelector("small").textContent = "Unavailable";
        });
        byId("workspaceNotice").textContent = `Workspace error: ${error.message}`;
        byId("workspaceNotice").className = "workspace-notice state-error";
    }
}

function setWorkspaceActionState(action, state, detail) {
    const button = byId(`action-${action}`);
    button.className = `state-${state}`;
    button.querySelector("span").textContent = workspaceLabels[action];
    button.querySelector("small").textContent = detail;
    button.disabled = state === "loading" || button.dataset.available !== "true";
}

async function runWorkspaceAction(action) {
    if (!currentTripId) return;
    if (action === "check_flight") {
        openFlightStatusForm();
        return;
    }
    await executeWorkspaceAction(action);
}

function openFlightStatusForm() {
    const saved = latestWorkspace?.monitoring?.flight || {};
    if (saved.flight_number) byId("monitorFlightNumber").value = saved.flight_number;
    if (saved.flight_date) byId("monitorFlightDate").value = saved.flight_date;
    byId("flightDetailsForm").classList.remove("hidden");
    byId("workspaceNotice").className = "workspace-notice";
    byId("workspaceNotice").textContent =
        "Enter the exact flight number and date to request real provider status.";
    byId("monitorFlightNumber").focus();
    byId("flightDetailsForm").scrollIntoView({behavior: "smooth", block: "center"});
}

async function executeWorkspaceAction(action) {
    const output = byId("workspaceOutput");
    setWorkspaceActionState(action, "loading", "Working…");
    byId("workspaceNotice").className = "workspace-notice";
    byId("workspaceNotice").textContent = `${workspaceLabels[action]} is running.`;
    try {
        const data = await requestJson(
            `/api/trips/${currentTripId}/workspace/actions/${action}`,
            {}
        );
        const noData = data.state === "no_data";
        const riskDetected = data.state === "risk" || data.result?.risk?.detected;
        setWorkspaceActionState(
            action,
            noData || riskDetected ? "warning" : "success",
            noData ? "No live match" : (riskDetected ? "Risk detected" : "✓ Complete")
        );
        byId("workspaceNotice").textContent = data.message;
        byId("workspaceNotice").className =
            `workspace-notice ${noData || riskDetected ? "state-warning" : "state-success"}`;
        renderWorkspaceResult(action, data.result);
        output.classList.remove("hidden");
        if (riskDetected) showRiskDecision(data.result.risk);
        else byId("riskDecision").classList.add("hidden");
        if (action === "check_flight") {
            showBookingNotification(
                noData
                    ? "Real provider checked: no live record was found for that flight and date."
                    : (riskDetected
                        ? "Flight disruption detected. Review the Trip Guardian choices."
                        : "Real flight status retrieved successfully.")
            );
        }
    } catch (error) {
        setWorkspaceActionState(action, "error", "Failed — retry");
        byId("workspaceNotice").textContent = error.message;
        byId("workspaceNotice").className = "workspace-notice state-error";
    }
}

function showRiskDecision(risk) {
    byId("riskTitle").textContent = risk.title || "Trip risk detected";
    byId("riskReasons").innerHTML = (risk.reasons || [])
        .map((reason) => `<li>${escapeHtml(reason)}</li>`)
        .join("");
    byId("riskPrompt").textContent =
        risk.prompt || "Would you like to recreate this trip or keep the current plan?";
    byId("riskDecision").classList.remove("hidden");
    byId("riskDecision").scrollIntoView({behavior: "smooth", block: "nearest"});
}

async function handleRiskDecision(decision) {
    const recreateButton = byId("recreateTripBtn");
    const skipButton = byId("skipRiskBtn");
    recreateButton.disabled = true;
    skipButton.disabled = true;
    const previous = recreateButton.textContent;
    if (decision === "recreate") recreateButton.textContent = "Recreating trip…";
    try {
        const data = await requestJson(
            `/api/trips/${currentTripId}/workspace/risk-decision`,
            {decision}
        );
        byId("riskDecision").classList.add("hidden");
        if (decision === "recreate") {
            renderWorkflowResult(data);
            await loadTrips();
            showBookingNotification("A replacement trip draft is ready for your approval.");
        } else {
            byId("workspaceNotice").className = "workspace-notice state-warning";
            byId("workspaceNotice").textContent = data.message;
            showBookingNotification("Current trip kept. The risk decision was saved.");
        }
    } catch (error) {
        byId("workspaceNotice").className = "workspace-notice state-error";
        byId("workspaceNotice").textContent = error.message;
    } finally {
        recreateButton.textContent = previous;
        recreateButton.disabled = false;
        skipButton.disabled = false;
    }
}

function displayValue(value, suffix = "") {
    return value === null || value === undefined || value === ""
        ? "Not available"
        : `${value}${suffix}`;
}

function statusClass(status) {
    const normalized = String(status || "").toLowerCase();
    if (["active", "landed", "scheduled"].includes(normalized)) return "status-good";
    if (["cancelled", "diverted"].includes(normalized)) return "status-bad";
    return "status-neutral";
}

function formatDateTime(value) {
    if (!value) return "Not available";
    const parsed = new Date(String(value).replace(" ", "T"));
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, {
        dateStyle: "medium",
        timeStyle: "short"
    }).format(parsed);
}

function formatDateOnly(value) {
    const parts = String(value || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) return value || "Not available";
    return new Intl.DateTimeFormat(undefined, {dateStyle: "medium"})
        .format(new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])));
}

function normalizeFlightNumber(value) {
    return String(value || "")
        .trim()
        .toUpperCase()
        .replace(/[\s/\-_\u2010-\u2015]+/g, "");
}

function renderWorkspaceResult(action, result) {
    const output = byId("workspaceOutput");
    output.classList.remove("raw-output");
    if (action === "check_flight" && result?.kind === "flight_status") {
        const cards = (result.matches || []).map((flight) => `
            <article class="status-card flight-status-card">
                <div class="status-card-header">
                    <div>
                        <span class="result-eyebrow">${escapeHtml(result.provider)} · real-time data</span>
                        <h4>${escapeHtml(flight.airline || "Airline unavailable")} ${escapeHtml(flight.flight || result.query?.flight_number || "")}</h4>
                    </div>
                    <span class="status-badge ${statusClass(flight.status)}">${escapeHtml(flight.status || "Unknown")}</span>
                </div>
                ${result.message ? `<p class="result-message">${escapeHtml(result.message)}</p>` : ""}
                <div class="route-grid">
                    ${renderAirportResult("Departure", flight.departure || {})}
                    <span class="route-arrow" aria-hidden="true">→</span>
                    ${renderAirportResult("Arrival", flight.arrival || {})}
                </div>
                <div class="result-meta">
                    <span><b>Requested date</b>${escapeHtml(formatDateOnly(result.query?.flight_date || flight.date))}</span>
                    <span><b>Flight number</b>${escapeHtml(flight.flight || result.query?.flight_number || "Not available")}</span>
                </div>
            </article>
        `).join("");
        output.innerHTML = cards || '<p class="result-message">No formatted flight result was returned.</p>';
        return;
    }

    if (action === "check_weather" && result?.kind === "weather") {
        const current = result.current || {};
        const forecast = (result.forecast || []).map((item) => `
            <li>
                <time>${escapeHtml(formatDateTime(item.datetime))}</time>
                <strong>${escapeHtml(displayValue(item.temperature_c, "°C"))}</strong>
                <span>${escapeHtml(item.condition || "Condition unavailable")}</span>
            </li>
        `).join("");
        output.innerHTML = `
            <article class="status-card weather-status-card">
                <div class="status-card-header">
                    <div>
                        <span class="result-eyebrow">OpenWeather · live conditions</span>
                        <h4>${escapeHtml(result.city || current.city || "Weather")}</h4>
                    </div>
                    <span class="weather-temperature">${escapeHtml(displayValue(current.temperature_c, "°C"))}</span>
                </div>
                <p class="weather-condition">${escapeHtml(current.condition || result.message || "Condition unavailable")}</p>
                <div class="weather-metrics">
                    <span><b>Feels like</b>${escapeHtml(displayValue(current.feels_like_c, "°C"))}</span>
                    <span><b>Humidity</b>${escapeHtml(displayValue(current.humidity, "%"))}</span>
                    <span><b>Wind</b>${escapeHtml(displayValue(current.wind_speed, " m/s"))}</span>
                </div>
                <h5>Upcoming forecast</h5>
                <ul class="forecast-list">${forecast || "<li>Forecast unavailable</li>"}</ul>
            </article>
        `;
        return;
    }

    output.classList.add("raw-output");
    output.textContent = typeof result === "string" ? result : JSON.stringify(result, null, 2);
}

function renderAirportResult(label, airport) {
    return `
        <section class="airport-result">
            <span>${escapeHtml(label)}</span>
            <h5>${escapeHtml(airport.iata || "N/A")}</h5>
            <p>${escapeHtml(airport.airport || "Airport unavailable")}</p>
            <dl>
                <div><dt>Scheduled</dt><dd>${escapeHtml(formatDateTime(airport.scheduled))}</dd></div>
                <div><dt>Terminal</dt><dd>${escapeHtml(airport.terminal || "N/A")}</dd></div>
                <div><dt>Gate</dt><dd>${escapeHtml(airport.gate || "N/A")}</dd></div>
                <div><dt>Delay</dt><dd>${escapeHtml(airport.delay || "N/A")}</dd></div>
            </dl>
        </section>
    `;
}

function showBookingNotification(message) {
    const toast = byId("appToast");
    toast.textContent = message;
    toast.classList.remove("hidden");
    window.clearTimeout(showBookingNotification.timeoutId);
    showBookingNotification.timeoutId = window.setTimeout(() => {
        toast.classList.add("hidden");
    }, 6000);
}

function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value ?? "";
    return element.innerHTML;
}

async function restoreSession() {
    try {
        const data = await requestJson("/api/auth/me", null, "GET");
        showAuthenticated(data.user);
        await loadTrips();
    } catch {
        showSignedOut();
    }
}

async function copyResult() {
    const text = byId("resultBox").innerText;
    if (!text) return;

    try {
        await navigator.clipboard.writeText(text);
        const button = document.querySelector(".copy-btn");
        const previous = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = previous; }, 1400);
    } catch {
        showError("The plan could not be copied.");
    }
}

function downloadPDF() {
    const pdfContent = byId("pdfContent");
    if (!latestAnswerMarkdown || !pdfContent) {
        showError("There is no travel plan to download.");
        return;
    }

    const button = document.querySelector(".download-btn");
    const previous = button.textContent;
    button.textContent = "Preparing...";
    button.disabled = true;

    html2pdf()
        .set({
            margin: 0.5,
            filename: "tripmate-travel-plan.pdf",
            image: {type: "jpeg", quality: 0.98},
            html2canvas: {scale: 2, useCORS: true, backgroundColor: "#ffffff"},
            jsPDF: {unit: "in", format: "a4", orientation: "portrait"},
            pagebreak: {mode: ["avoid-all", "css", "legacy"]}
        })
        .from(pdfContent)
        .save()
        .catch(() => showError("The PDF could not be generated."))
        .finally(() => {
            button.textContent = previous;
            button.disabled = false;
        });
}

document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key === "Enter") sendMessage();
});

byId("authForm").addEventListener("submit", (event) => {
    event.preventDefault();
    submitAuth();
});

byId("authModeBtn").addEventListener("click", toggleAuthMode);

byId("flightDetailsForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = byId("saveFlightDetailsBtn");
    const flightInput = byId("monitorFlightNumber");
    const flightNumber = normalizeFlightNumber(flightInput.value);
    if (!/^[A-Z0-9]{3,10}$/.test(flightNumber)) {
        byId("workspaceNotice").className = "workspace-notice state-error";
        byId("workspaceNotice").textContent =
            "Enter a valid flight number, for example AI171 or AI-171.";
        flightInput.focus();
        return;
    }
    flightInput.value = flightNumber;
    button.disabled = true;
    button.textContent = "Checking real status…";
    setWorkspaceActionState("check_flight", "loading", "Checking provider…");
    try {
        const data = await requestJson(
            `/api/trips/${currentTripId}/workspace/flight-details`,
            {
                flight_number: flightNumber,
                flight_date: byId("monitorFlightDate").value
            }
        );
        applyWorkspace(data.workspace);
        await executeWorkspaceAction("check_flight");
    } catch (error) {
        setWorkspaceActionState("check_flight", "error", "Failed — retry");
        byId("workspaceNotice").className = "workspace-notice state-error";
        byId("workspaceNotice").textContent = error.message;
    } finally {
        button.disabled = false;
        button.textContent = "Check real status";
    }
});

byId("passwordToggle").addEventListener("click", () => {
    const password = byId("password");
    const showing = password.type === "text";
    password.type = showing ? "password" : "text";
    byId("passwordToggle").textContent = showing ? "Show" : "Hide";
    byId("passwordToggle").setAttribute("aria-label", showing ? "Show password" : "Hide password");
    byId("passwordToggle").setAttribute("aria-pressed", String(!showing));
    password.focus();
});

restoreSession();
