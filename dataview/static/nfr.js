// Live filtering for the novel foods table. The server applies the same
// filters on form submit, so the page still works without JavaScript.
(() => {
    "use strict";

    const form = document.getElementById("filters");
    const table = document.getElementById("records");
    if (!form || !table) return;

    document.documentElement.classList.add("js");

    const count = document.getElementById("result-count");
    const empty = document.getElementById("no-results");
    const fields = ["q", "food", "justification", "outcome"].map((name) => form.elements[name]);

    const rows = Array.from(table.tBodies[0].rows, (row) => ({
        row,
        q: row.textContent.toLowerCase(),
        food: row.cells[0].textContent.toLowerCase(),
        justification: row.cells[2].textContent.toLowerCase(),
        tones: row.dataset.tones.split(" "),
    }));

    // Match each term at the start of a word, so "lion" finds "Lion's mane"
    // but not "million". Mirrors _word_start() in views.py.
    const terms = (value) =>
        value
            .trim()
            .toLowerCase()
            .split(/\s+/)
            .filter(Boolean)
            .map((t) => new RegExp(`(?<![\\p{L}\\p{N}])${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "u"));

    function apply() {
        const q = terms(form.elements.q.value);
        const food = terms(form.elements.food.value);
        const justification = terms(form.elements.justification.value);
        const outcome = form.elements.outcome.value;

        let visible = 0;
        for (const entry of rows) {
            const match =
                (!outcome || entry.tones.includes(outcome)) &&
                q.every((t) => t.test(entry.q)) &&
                food.every((t) => t.test(entry.food)) &&
                justification.every((t) => t.test(entry.justification));
            entry.row.hidden = !match;
            if (match) visible += 1;
        }
        count.textContent = `Showing ${visible} of ${rows.length} entries`;
        empty.hidden = visible > 0;

        // Keep the URL shareable without adding a history entry per keystroke.
        const params = new URLSearchParams();
        for (const field of fields) {
            if (field.value.trim()) params.set(field.name, field.value.trim());
        }
        const query = params.toString();
        history.replaceState(null, "", query ? `?${query}` : location.pathname);
    }

    let timer;
    form.addEventListener("input", () => {
        clearTimeout(timer);
        timer = setTimeout(apply, 120);
    });
    form.addEventListener("submit", (event) => {
        event.preventDefault();
        apply();
    });
    document.getElementById("reset").addEventListener("click", (event) => {
        event.preventDefault();
        form.reset();
        for (const field of fields) field.value = "";
        apply();
        form.elements.q.focus();
    });
})();
