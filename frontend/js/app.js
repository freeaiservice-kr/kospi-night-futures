/**
 * Alpine.js application entry point — init only.
 * Logic lives in futuresStore.js, optionsStore.js, uiStore.js
 */

import './futuresStore.js';
import './optionsStore.js';
import './uiStore.js';
import './sectorStore.js';
import './leaderStore.js';

document.addEventListener('alpine:init', () => {
  Alpine.data('dashboardApp', () => ({
    init() {
      Alpine.store('futures').init();
      Alpine.store('options').init();
    },
  }));
});
