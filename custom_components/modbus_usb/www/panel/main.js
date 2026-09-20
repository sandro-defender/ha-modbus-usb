/* main.js — Top-level event wiring. Must load last.
 * Split from modbus-panel.html; loaded as a classic script (global scope).
 * Load order is defined by the <script> tags in modbus-panel.html.
 */
    document.addEventListener('keydown', (event) => {
      const tabList = event.target.closest('[role="tablist"]');
      if (!tabList || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      const tabs = [...tabList.querySelectorAll('[role="tab"]')];
      const currentIndex = tabs.indexOf(event.target);
      if (currentIndex === -1) return;
      event.preventDefault();
      const nextIndex = event.key === 'Home' ? 0
        : event.key === 'End' ? tabs.length - 1
        : (currentIndex + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      const nextTab = tabs[nextIndex];
      switchTab(nextTab.id.replace('tab-btn-', ''));
      nextTab.focus();
    });

    // Launch!
    window.addEventListener('DOMContentLoaded', init);
