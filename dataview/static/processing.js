// Poll until the background parse finishes, then load the data page.
(() => {
    "use strict";

    const section = document.getElementById("processing");
    if (!section) return;

    const status = document.getElementById("status");
    const elapsed = document.getElementById("elapsed");
    const started = Date.now();
    let delay = 2000;

    const ticker = setInterval(() => {
        const seconds = Math.round((Date.now() - started) / 1000);
        elapsed.textContent = `(${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")})`;
    }, 1000);

    function fail() {
        clearInterval(ticker);
        section.classList.add("failed");
        status.textContent = "Sorry, the PDF could not be processed. ";
        const retry = document.createElement("a");
        retry.href = section.dataset.doneUrl;
        retry.textContent = "Try again";
        status.append(retry);
    }

    async function poll() {
        try {
            const response = await fetch(section.dataset.statusUrl, {
                cache: "no-store",
                headers: { Accept: "application/json" },
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const { state } = await response.json();
            if (state === "complete" || state === "idle") {
                window.location.assign(section.dataset.doneUrl);
                return;
            }
            if (state === "error") {
                fail();
                return;
            }
            delay = 2000;
        } catch {
            // Network blip or restart: keep trying, but back off.
            delay = Math.min(delay * 2, 30000);
        }
        setTimeout(poll, delay);
    }

    setTimeout(poll, delay);
})();
