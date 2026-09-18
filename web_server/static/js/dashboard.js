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

// Read History Tracking
// 点击任意新闻标题链接时，上报到后端，供概览页「最近阅读」展示
(function () {
  document.addEventListener('click', function (e) {
    const link = e.target.closest('.report-news-link');
    if (!link) return;

    try {
      const payload = JSON.stringify({
        title: link.dataset.title || link.textContent.trim(),
        url: link.href,
        platform: link.dataset.platform || '',
      });
      navigator.sendBeacon('/api/read-history', new Blob([payload], { type: 'application/json' }));
    } catch {
      // 阅读记录上报失败不影响正常跳转
    }
  });
})();

let currentNewsBatch = 0;

function showNextNewsBatch() {
  const grid = document.getElementById('newsGrid');
  if (!grid) return;
  let totalBatches = parseInt(grid.dataset.totalBatches || '1', 10);
  if (!Number.isFinite(totalBatches) || totalBatches <= 1) return;

  currentNewsBatch = (currentNewsBatch + 1) % totalBatches;

  grid.querySelectorAll('.news-card').forEach(function (card) {
    const batchIndex = parseInt(card.getAttribute('data-batch'), 10);
    card.style.display = batchIndex === currentNewsBatch ? '' : 'none';
  });

  const indicator = document.getElementById('currentNewsBatchNum');
  if (indicator) {
    indicator.textContent = currentNewsBatch + 1;
  }
}
