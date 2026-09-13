let currentThreadId = null;
let latestAnswerMarkdown = "";

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

function renderMarkdown(markdown) {
    const resultBox = byId("resultBox");
    if (typeof marked !== "undefined" && typeof DOMPurify !== "undefined") {
        resultBox.innerHTML = DOMPurify.sanitize(marked.parse(markdown));
        return;
    }
    resultBox.textContent = markdown;
}

function renderWorkflowResult(data) {
    currentThreadId = data.thread_id;
    latestAnswerMarkdown = data.answer || data.itinerary || "";
    localStorage.setItem("travel_thread_id", currentThreadId);

    renderMarkdown(latestAnswerMarkdown);
    byId("threadInfo").textContent = `Plan reference: ${currentThreadId}`;

    const awaitingReview = data.status === "approval_required";
    byId("approvalPanel").classList.toggle("hidden", !awaitingReview);
    byId("resultTitle").textContent = awaitingReview ? "Draft travel plan" : "Approved travel plan";

    if (!awaitingReview) {
        byId("revisionFeedback").value = "";
    }

    byId("resultSection").classList.remove("hidden");
    byId("resultSection").scrollIntoView({behavior: "smooth", block: "start"});
}

async function requestJson(url, payload) {
    const response = await fetch(url, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(payload)
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
        throw new Error(data.error || "The request could not be completed.");
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
    } catch (error) {
        showError(error.message);
    } finally {
        setBusy(false);
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
