/**
 * Alpine.store('ui') — UI state (drilldowns, toggles, mobile segment)
 */

document.addEventListener('alpine:init', () => {
  Alpine.store('ui', {
    // Mobile segment (1024px 이하)
    activeTab: 'futures',

    // Options board drilldown
    expandedBoard: false,
    selectedStrike: null,

    // History toggles
    expandedInvestorHistory: false,
    expandedFuturesHistory: false,
  });
});
