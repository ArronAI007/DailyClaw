/**
 * DailyClaw Dashboard JavaScript
 */

// Theme Management
(function () {
  const THEME_KEY = 'dailyclaw-theme';

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_KEY);
    } catch {
      return null;
    }
  }

  function setStoredTheme(theme) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // ignore
    }
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function initTheme() {
    const stored = getStoredTheme();
    if (stored) {
      applyTheme(stored);
    } else {
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      applyTheme(prefersDark ? 'dark' : 'light');
    }
  }

  function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    setStoredTheme(next);
  }

  window.toggleTheme = toggleTheme;
  initTheme();
})();

// Sidebar / Mobile Menu
(function () {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  const menuToggle = document.getElementById('menuToggle');
  const sidebarClose = document.getElementById('sidebarClose');

  function openSidebar() {
    sidebar?.classList.add('open');
    overlay?.classList.add('open');
  }

  function closeSidebar() {
    sidebar?.classList.remove('open');
    overlay?.classList.remove('open');
  }

  menuToggle?.addEventListener('click', openSidebar);
  sidebarClose?.addEventListener('click', closeSidebar);
  overlay?.addEventListener('click', closeSidebar);
})();

// Config Tabs
(function () {
  document.querySelectorAll('.config-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.config-tab').forEach((t) => t.classList.remove('active'));
      document.querySelectorAll('.config-panel').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      const panel = document.querySelector(`[data-panel="${tab.dataset.tab}"]`);
      panel?.classList.add('active');
    });
  });
})();

// Theme toggle button
document.getElementById('themeToggle')?.addEventListener('click', window.toggleTheme);
