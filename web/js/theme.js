// Dark only. Inter is the UI face. Accent palette still lives here because
// this file paints before body so the first frame is already dark.

const THEME_ACCENTS = [
    { id: "carrot", label: "Carrot", dot: "#f4813f" },
    { id: "ember", label: "Ember", dot: "#f2603f" },
    { id: "amber", label: "Amber", dot: "#e3a33a" },
    { id: "orchid", label: "Orchid", dot: "#b57ae0" },
    { id: "teal", label: "Teal", dot: "#3fbfa8" },
    { id: "indigo", label: "Indigo", dot: "#7c8cf0" },
];
const MODE_KEY = "carrot.theme";
const ACCENT_KEY = "carrot.accent";

function readPref(key, allowed, fallback) {
    try {
        const v = localStorage.getItem(key);
        return allowed.includes(v) ? v : fallback;
    } catch (e) {
        return fallback;
    }
}

let themeAccent = readPref(ACCENT_KEY, THEME_ACCENTS.map((a) => a.id), "carrot");

function applyTheme() {
    const root = document.documentElement;
    root.setAttribute("data-theme", "dark");
    root.setAttribute("data-accent", themeAccent);
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) meta.setAttribute("content", "dark");
    try {
        localStorage.setItem(MODE_KEY, "dark");
    } catch (e) { /* private mode */ }
    if (window.carrotAPI && window.carrotAPI.setAppearance) {
        const style = getComputedStyle(root);
        const background = style.getPropertyValue("--bg").trim();
        if (/^#[0-9a-fA-F]{6}$/.test(background)) {
            Promise.resolve(
                window.carrotAPI.setAppearance({
                    background,
                    theme: "dark",
                    accent: themeAccent,
                })
            ).catch(() => {});
        }
    }
    window.dispatchEvent(
        new CustomEvent("carrot-theme", {
            detail: { mode: "dark", resolved: "dark", accent: themeAccent },
        })
    );
}

applyTheme();

function persistAccent(value) {
    try {
        localStorage.setItem(ACCENT_KEY, value);
    } catch (e) { /* ignore */ }
    if (typeof api === "function") {
        api("/api/config/ui_accent", {
            method: "PUT",
            body: JSON.stringify(value),
        }).catch(() => {});
        api("/api/config/ui_theme", {
            method: "PUT",
            body: JSON.stringify("dark"),
        }).catch(() => {});
    }
}

function setThemeAccent(accent) {
    if (!THEME_ACCENTS.some((a) => a.id === accent)) return;
    themeAccent = accent;
    applyTheme();
    persistAccent(accent);
    renderThemePicker();
}

async function syncThemeFromServer() {
    if (typeof api !== "function") return;
    let cfg;
    try {
        cfg = await api("/api/config");
    } catch (e) {
        return;
    }
    let storedAccent = null;
    try {
        storedAccent = localStorage.getItem(ACCENT_KEY);
    } catch (e) { /* ignore */ }
    if (!storedAccent && THEME_ACCENTS.some((a) => a.id === cfg.ui_accent)) {
        themeAccent = cfg.ui_accent;
        applyTheme();
    }
    renderThemePicker();
}

function renderThemePicker() {
    const modes = document.getElementById("theme-modes");
    if (modes) modes.innerHTML = "";
    const accents = document.getElementById("theme-accents");
    if (!accents) return;
    accents.innerHTML = THEME_ACCENTS.map(
        (a) => `
            <button type="button" class="theme-swatch" data-accent="${a.id}"
                    aria-pressed="${a.id === themeAccent}">
              <span class="theme-dot" style="background:${a.dot}"></span>
              <span>${a.label}</span>
            </button>`
    ).join("");
    accents.querySelectorAll("[data-accent]").forEach((el) => {
        el.onclick = () => setThemeAccent(el.dataset.accent);
    });
}

window.addEventListener("DOMContentLoaded", () => {
    applyTheme();
    renderThemePicker();
    syncThemeFromServer();
});
