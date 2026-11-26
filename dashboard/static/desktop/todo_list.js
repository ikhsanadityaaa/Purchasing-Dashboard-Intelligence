/*
 * Serveone Dashboard Desktop: To Do List (Two-Table Priority Follow-Up Workspace)
 * Table 1: Pending Delivery Follow-Up (SO Status priority checklist & dropdown filters)
 * Table 2: Item Registration Follow-Up (Proc. Status priority checklist & dropdown filters)
 * Auto-locks/persists every filter & priority change to SQLite disk preferences + localStorage.
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'po-monitoring:todo-list:locked-prefs-v2';
  const COL_WIDTHS_KEY = 'po-monitoring:todo-list:col-widths-v1';

  const DEFAULT_SO_COL_WIDTHS = {
    'No': 54,
    'SO Status & Prioritas': 210,
    'Aging': 74,
    'SO Create Date': 112,
    'Req. Dlv. Date': 112,
    'PIC': 82,
    'SO No.': 114,
    'Item': 116,
    'PO No.': 132,
    'Op. Unit Nm.': 220,
    'Vendor': 190,
    'Prod. ID': 102,
    'Product Name': 210,
    'Specification': 230,
    'Qty': 76,
    'Amount (IDR)': 136,
    'Remarks': 180,
  };

  const DEFAULT_IR_COL_WIDTHS = {
    'No': 54,
    'Proc. Status & Prioritas': 210,
    'Aging': 74,
    'Req. Date': 104,
    'Op. Unit / Client': 220,
    'PIC': 82,
    'Req. No.': 116,
    'Origin Nm.': 120,
    'Stock No.': 110,
    'Prod. ID': 104,
    'Prod. Nm.': 200,
    'Spec.': 230,
    'Mfr. Nm.': 160,
    'Unit': 74,
    'Remarks': 180,
  };

  function loadColWidths() {
    try {
      const raw = window.localStorage.getItem(COL_WIDTHS_KEY);
      if (!raw) return { so: Object.assign({}, DEFAULT_SO_COL_WIDTHS), ir: Object.assign({}, DEFAULT_IR_COL_WIDTHS) };
      const parsed = JSON.parse(raw);
      return {
        so: Object.assign({}, DEFAULT_SO_COL_WIDTHS, (parsed && parsed.so) || {}),
        ir: Object.assign({}, DEFAULT_IR_COL_WIDTHS, (parsed && parsed.ir) || {}),
      };
    } catch (_) {
      return { so: Object.assign({}, DEFAULT_SO_COL_WIDTHS), ir: Object.assign({}, DEFAULT_IR_COL_WIDTHS) };
    }
  }

  function saveColWidths(widths) {
    try {
      window.localStorage.setItem(COL_WIDTHS_KEY, JSON.stringify(widths));
    } catch (_) {}
  }

  function loadLocalPrefs() {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (_) {
      return null;
    }
  }

  function saveLocalPrefs(prefs) {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
    } catch (_) {}
  }

  function fmtInt(val) {
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Number(val) || 0);
  }

  function fmtCompactIdr(val) {
    const n = Number(val) || 0;
    const a = Math.abs(n);
    if (a <= 0) return 'IDR 0';
    if (a >= 1e12) return 'IDR ' + (n / 1e12).toFixed(2) + 'T';
    if (a >= 1e9) return 'IDR ' + (n / 1e9).toFixed(1) + 'B';
    if (a >= 1e6) return 'IDR ' + (n / 1e6).toFixed(1) + 'M';
    if (a >= 1e3) return 'IDR ' + (n / 1e3).toFixed(0) + 'K';
    return 'IDR ' + fmtInt(n);
  }

  function fmtFullIdr(val) {
    const n = Number(val) || 0;
    if (n <= 0) return '-';
    return 'IDR ' + new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(n);
  }

  function fmtDateShort(val) {
    if (!val) return '-';
    const s = String(val).slice(0, 10);
    const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    return m ? m[3] + '/' + m[2] + '/' + m[1] : s;
  }

  function buildStatusMapFromList(list) {
    const out = {};
    (list || []).forEach((item, idx) => {
      if (!item || !item.status) return;
      out[item.status] = {
        checked: Boolean(item.checked),
        priority: Boolean(item.priority),
        order: Number(item.order) || idx + 1,
      };
    });
    return out;
  }

  function reindexStatusList(list) {
    const sorted = [...list].sort((a, b) => {
      const ap = a.priority ? 0 : 1;
      const bp = b.priority ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return (Number(a.order) || 999) - (Number(b.order) || 999);
    });
    let prioRank = 0;
    return sorted.map((item, idx) => {
      const copy = Object.assign({}, item, { order: idx + 1 });
      if (copy.priority) {
        prioRank += 1;
        copy.priority_rank = prioRank;
      } else {
        copy.priority_rank = null;
      }
      return copy;
    });
  }

  function svgIcon(paths, sizeClass) {
    return function (h) {
      return h(
        'svg',
        {
          viewBox: '0 0 24 24',
          fill: 'none',
          stroke: 'currentColor',
          strokeWidth: '2',
          strokeLinecap: 'round',
          strokeLinejoin: 'round',
          className: sizeClass || 'w-4 h-4 flex-shrink-0',
          'aria-hidden': 'true',
        },
        ...paths.map((d, i) => h('path', { key: i, d }))
      );
    };
  }

  const ICON_BOX = svgIcon([
    'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z',
    'M3.27 6.96L12 12.01l8.73-5.05',
    'M12 22.08V12',
  ], 'w-5 h-5 text-blue-500 flex-shrink-0');

  const ICON_CLIPBOARD = svgIcon([
    'M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2',
    'M9 3h6a2 2 0 0 1 2 2v0a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v0a2 2 0 0 1 2-2z',
    'M9 12h6',
    'M9 16h6',
  ], 'w-5 h-5 text-blue-500 flex-shrink-0');

  const ICON_SEARCH = svgIcon(['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.35-4.35']);
  const ICON_FILTER = svgIcon(['M22 3H2l8 9.46V19l4 2v-8.54L22 3z']);
  const ICON_LIST = svgIcon(['M9 11l3 3L22 4', 'M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11']);
  const ICON_DOWNLOAD = svgIcon(['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M7 10l5 5 5-5', 'M12 15V3']);
  const ICON_CHEVRON = svgIcon(['M6 9l6 6 6-6'], 'w-3.5 h-3.5 flex-shrink-0');
  const ICON_CHEVRON_UP = svgIcon(['M18 15l-6-6-6 6'], 'w-3.5 h-3.5 flex-shrink-0');
  const ICON_CHEVRON_LEFT = svgIcon(['M15 18l-6-6 6-6'], 'w-4 h-4 flex-shrink-0');
  const ICON_CHEVRON_RIGHT = svgIcon(['M9 18l6-6-6-6'], 'w-4 h-4 flex-shrink-0');
  const ICON_USER = svgIcon([
    'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2',
    'M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  ], 'w-5 h-5 flex-shrink-0');

  function copyColumnValues(rows, label, getter, onDone) {
    if (!rows || !rows.length) return;
    const values = rows.map((row, idx) => {
      const v = getter(row, idx);
      return v == null ? '' : String(v);
    });
    const text = values.join('\n');
    const notify = () => {
      if (typeof onDone === 'function') {
        onDone('Copied ' + values.length + ' values from "' + label + '" column');
      }
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(notify).catch(() => {
        fallbackCopyText(text);
        notify();
      });
    } else {
      fallbackCopyText(text);
      notify();
    }
  }

  function fallbackCopyText(text) {
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    } catch (_) {}
  }

  function useWindowStickyHeader(React, wrapRef, darkMode, keyDeps) {
    React.useEffect(() => {
      const wrap = wrapRef.current;
      if (!wrap || typeof document === 'undefined') return undefined;

      let overlay = null;
      let cloneTable = null;
      let lastHeadHtml = '';
      let rafId = null;

      const destroyOverlay = () => {
        if (overlay) {
          overlay.remove();
          overlay = null;
          cloneTable = null;
          lastHeadHtml = '';
        }
      };

      const ensureOverlay = () => {
        if (overlay) return;
        overlay = document.createElement('div');
        overlay.className = 'window-sticky-table-header' + (darkMode ? ' window-sticky-table-header-dark' : '');
        overlay.style.display = 'none';
        overlay.style.position = 'fixed';
        overlay.style.top = '0px';
        overlay.style.overflow = 'hidden';
        overlay.style.pointerEvents = 'none';
        overlay.style.zIndex = '39';
        overlay.style.boxSizing = 'border-box';
        cloneTable = document.createElement('table');
        cloneTable.className = 'window-sticky-table-header-table';
        cloneTable.style.borderCollapse = 'collapse';
        cloneTable.style.tableLayout = 'fixed';
        cloneTable.style.margin = '0';
        overlay.appendChild(cloneTable);
        document.body.appendChild(overlay);
      };

      const sync = () => {
        const table = wrap.querySelector('table');
        const thead = table ? table.querySelector('thead') : null;
        const tbody = table ? table.querySelector('tbody') : null;
        if (!table || !thead || !tbody) {
          destroyOverlay();
          return;
        }
        const wrapRect = wrap.getBoundingClientRect();
        const headRect = thead.getBoundingClientRect();
        const bodyRect = tbody.getBoundingClientRect();
        const shouldShow =
          headRect.top < 0 &&
          bodyRect.bottom > headRect.height + 8 &&
          wrapRect.width > 0;

        if (!shouldShow) {
          if (overlay) overlay.style.display = 'none';
          return;
        }

        ensureOverlay();
        overlay.className = 'window-sticky-table-header' + (darkMode ? ' window-sticky-table-header-dark' : '');
        overlay.style.left = Math.max(0, wrapRect.left) + 'px';
        overlay.style.width = wrapRect.width + 'px';
        overlay.style.height = headRect.height + 'px';
        overlay.style.display = 'block';

        const html = thead.outerHTML;
        if (html !== lastHeadHtml) {
          cloneTable.innerHTML = html;
          lastHeadHtml = html;
        }
        cloneTable.className = (table.className || '') + ' window-sticky-table-header-table';
        cloneTable.style.width = table.getBoundingClientRect().width + 'px';
        cloneTable.style.transform = 'translate3d(' + (-wrap.scrollLeft) + 'px, 0, 0)';

        const srcThs = Array.from(thead.querySelectorAll('th'));
        const dstThs = Array.from(cloneTable.querySelectorAll('th'));
        for (let i = 0; i < srcThs.length && i < dstThs.length; i++) {
          const w = srcThs[i].getBoundingClientRect().width;
          dstThs[i].style.width = w + 'px';
          dstThs[i].style.minWidth = w + 'px';
          dstThs[i].style.maxWidth = w + 'px';
          dstThs[i].style.boxSizing = 'border-box';
        }
      };

      const scheduleSync = () => {
        if (rafId != null) return;
        rafId = window.requestAnimationFrame(() => {
          rafId = null;
          sync();
        });
      };

      window.addEventListener('scroll', scheduleSync, { passive: true });
      window.addEventListener('resize', scheduleSync, { passive: true });
      wrap.addEventListener('scroll', scheduleSync, { passive: true });
      scheduleSync();

      return () => {
        if (rafId != null) window.cancelAnimationFrame(rafId);
        window.removeEventListener('scroll', scheduleSync);
        window.removeEventListener('resize', scheduleSync);
        wrap.removeEventListener('scroll', scheduleSync);
        destroyOverlay();
      };
    }, [React, wrapRef, darkMode, ...keyDeps]);
  }

  function ServeoneTodoListPage(props) {
    const React = props.React || window.React;
    const h = React.createElement;
    const darkMode = Boolean(props.darkMode);
    const dataRevision = props.dataRevision || '';

    const initialLocal = React.useMemo(() => loadLocalPrefs() || {}, []);

    const [selectedPics, setSelectedPics] = React.useState(() =>
      Array.isArray(initialLocal.selected_pics) ? initialLocal.selected_pics : []
    );
    const [soPriorityOnly, setSoPriorityOnly] = React.useState(() =>
      Boolean(initialLocal.so_priority_only ?? initialLocal.priority_only ?? false)
    );
    const [irPriorityOnly, setIrPriorityOnly] = React.useState(() =>
      Boolean(initialLocal.ir_priority_only ?? initialLocal.priority_only ?? false)
    );
    const [soSortBy, setSoSortBy] = React.useState(() => initialLocal.so_sort_by || 'priority_oldest');
    const [irSortBy, setIrSortBy] = React.useState(() => initialLocal.ir_sort_by || 'priority_oldest');
    const [soStatusesMap, setSoStatusesMap] = React.useState(() => initialLocal.so_statuses || {});
    const [procStatusesMap, setProcStatusesMap] = React.useState(() => initialLocal.proc_statuses || {});
    const [prefsHydrated, setPrefsHydrated] = React.useState(false);
    const [shouldSaveOnNextFetch, setShouldSaveOnNextFetch] = React.useState(false);

    // Independent table search, page, perPage, and dropdown open states
    const [soSearchDraft, setSoSearchDraft] = React.useState('');
    const [soSearch, setSoSearch] = React.useState('');
    const [soClients, setSoClients] = React.useState([]);
    const [soVendors, setSoVendors] = React.useState([]);
    const [soPage, setSoPage] = React.useState(1);
    const [soPerPage, setSoPerPage] = React.useState(25);

    const [irSearchDraft, setIrSearchDraft] = React.useState('');
    const [irSearch, setIrSearch] = React.useState('');
    const [irClients, setIrClients] = React.useState([]);
    const [irVendors, setIrVendors] = React.useState([]);
    const [irPage, setIrPage] = React.useState(1);
    const [irPerPage, setIrPerPage] = React.useState(25);
    const [multiFilterSearch, setMultiFilterSearch] = React.useState('');

    // Popover / drawer / active status card / smooth accordion / column resize / toast states
    const [openPopover, setOpenPopover] = React.useState(null);
    const [soFilterDrawerOpen, setSoFilterDrawerOpen] = React.useState(false);
    const [irFilterDrawerOpen, setIrFilterDrawerOpen] = React.useState(false);
    const [activeSoCard, setActiveSoCard] = React.useState(null); // null | '__ALL__' | status string
    const [activeIrCard, setActiveIrCard] = React.useState(null); // null | '__ALL__' | status string
    const [soMountedCard, setSoMountedCard] = React.useState(null);
    const [soExpanded, setSoExpanded] = React.useState(false);
    const [soSettled, setSoSettled] = React.useState(false);
    const [irMountedCard, setIrMountedCard] = React.useState(null);
    const [irExpanded, setIrExpanded] = React.useState(false);
    const [irSettled, setIrSettled] = React.useState(false);

    // Smooth accordion mount/unmount lifecycle for Pending Delivery table
    React.useEffect(() => {
      if (activeSoCard) {
        setSoMountedCard(activeSoCard);
        setSoSettled(false);
        let settleTimer = null;
        const raf = window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            setSoExpanded(true);
            settleTimer = window.setTimeout(() => setSoSettled(true), 430);
          });
        });
        return () => {
          window.cancelAnimationFrame(raf);
          if (settleTimer) window.clearTimeout(settleTimer);
        };
      } else {
        setSoSettled(false);
        setSoExpanded(false);
        const t = window.setTimeout(() => setSoMountedCard(null), 430);
        return () => window.clearTimeout(t);
      }
    }, [activeSoCard]);

    // Smooth accordion mount/unmount lifecycle for Item Registration table
    React.useEffect(() => {
      if (activeIrCard) {
        setIrMountedCard(activeIrCard);
        setIrSettled(false);
        let settleTimer = null;
        const raf = window.requestAnimationFrame(() => {
          window.requestAnimationFrame(() => {
            setIrExpanded(true);
            settleTimer = window.setTimeout(() => setIrSettled(true), 430);
          });
        });
        return () => {
          window.cancelAnimationFrame(raf);
          if (settleTimer) window.clearTimeout(settleTimer);
        };
      } else {
        setIrSettled(false);
        setIrExpanded(false);
        const t = window.setTimeout(() => setIrMountedCard(null), 430);
        return () => window.clearTimeout(t);
      }
    }, [activeIrCard]);
    const [colWidths, setColWidths] = React.useState(() => loadColWidths());
    const resizingRef = React.useRef(false);
    const [dragInfo, setDragInfo] = React.useState(null); // { kind: 'so'|'proc', status: string } | null
    const [dragOverInfo, setDragOverInfo] = React.useState(null); // { kind: 'so'|'proc', status: string } | null
    const [toastMsg, setToastMsg] = React.useState('');
    const toastTimerRef = React.useRef(null);
    const soTableWrapRef = React.useRef(null);
    const irTableWrapRef = React.useRef(null);

    const showToast = React.useCallback((msg) => {
      setToastMsg(msg);
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
      toastTimerRef.current = window.setTimeout(() => setToastMsg(''), 2500);
    }, []);

    const startColResize = React.useCallback((e, kind, colKey, currentWidth) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const startW = Number(currentWidth) || 120;
      resizingRef.current = true;
      const onMove = (ev) => {
        const nextW = Math.min(900, Math.max(48, Math.round(startW + (ev.clientX - startX))));
        setColWidths((prev) => {
          const next = {
            so: Object.assign({}, prev.so),
            ir: Object.assign({}, prev.ir),
          };
          next[kind][colKey] = nextW;
          return next;
        });
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        setColWidths((prev) => {
          saveColWidths(prev);
          return prev;
        });
        window.setTimeout(() => {
          resizingRef.current = false;
        }, 50);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    }, []);

    const resetColWidths = React.useCallback((kind) => {
      setColWidths((prev) => {
        const next = {
          so: kind === 'so' ? Object.assign({}, DEFAULT_SO_COL_WIDTHS) : Object.assign({}, prev.so),
          ir: kind === 'ir' ? Object.assign({}, DEFAULT_IR_COL_WIDTHS) : Object.assign({}, prev.ir),
        };
        saveColWidths(next);
        return next;
      });
    }, []);

    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');
    const [payload, setPayload] = React.useState(null);

    useWindowStickyHeader(React, soTableWrapRef, darkMode, [payload, activeSoCard, soPerPage, soPage, colWidths.so]);
    useWindowStickyHeader(React, irTableWrapRef, darkMode, [payload, activeIrCard, irPerPage, irPage, colWidths.ir]);

    // Hydrate saved preferences from disk on initial mount
    React.useEffect(() => {
      let alive = true;
      (async () => {
        try {
          const res = await fetch('/api/todo-list/preferences', {
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json' },
          });
          if (res.ok) {
            const json = await res.json();
            const srv = (json && json.preferences) || {};
            if (alive && srv && srv.updated_at) {
              if (Array.isArray(srv.selected_pics)) setSelectedPics(srv.selected_pics);
              if (typeof srv.so_priority_only === 'boolean') setSoPriorityOnly(srv.so_priority_only);
              else if (typeof srv.priority_only === 'boolean') setSoPriorityOnly(srv.priority_only);
              if (typeof srv.ir_priority_only === 'boolean') setIrPriorityOnly(srv.ir_priority_only);
              else if (typeof srv.priority_only === 'boolean') setIrPriorityOnly(srv.priority_only);
              if (srv.so_sort_by) setSoSortBy(srv.so_sort_by);
              if (srv.ir_sort_by) setIrSortBy(srv.ir_sort_by);
              if (srv.so_statuses && typeof srv.so_statuses === 'object') setSoStatusesMap(srv.so_statuses);
              if (srv.proc_statuses && typeof srv.proc_statuses === 'object') setProcStatusesMap(srv.proc_statuses);
              saveLocalPrefs(srv);
            }
          }
        } catch (_) {
        } finally {
          if (alive) setPrefsHydrated(true);
        }
      })();
      return () => {
        alive = false;
      };
    }, []);

    // Close popovers on Escape key
    React.useEffect(() => {
      if (!openPopover) return;
      const onKey = (e) => {
        if (e.key === 'Escape') setOpenPopover(null);
      };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [openPopover]);

    // Auto-save to localStorage whenever any persistent filter changes
    React.useEffect(() => {
      if (!prefsHydrated) return;
      saveLocalPrefs({
        selected_pics: selectedPics,
        so_priority_only: soPriorityOnly,
        ir_priority_only: irPriorityOnly,
        so_sort_by: soSortBy,
        ir_sort_by: irSortBy,
        so_statuses: soStatusesMap,
        proc_statuses: procStatusesMap,
      });
    }, [
      prefsHydrated,
      selectedPics,
      soPriorityOnly,
      irPriorityOnly,
      soSortBy,
      irSortBy,
      soStatusesMap,
      procStatusesMap,
    ]);

    const fetchQueueData = React.useCallback(async () => {
      if (!prefsHydrated) return;
      setLoading(true);
      setError('');
      const doSave = shouldSaveOnNextFetch;
      if (doSave) setShouldSaveOnNextFetch(false);
      try {
        const headers = { 'Content-Type': 'application/json', Accept: 'application/json' };
        if (window.__serveoneDesktopUploadToken) {
          headers['X-Serveone-Desktop-Upload'] = window.__serveoneDesktopUploadToken;
        }
        const res = await fetch('/api/todo-list/data', {
          method: 'POST',
          credentials: 'same-origin',
          cache: 'no-store',
          headers,
          body: JSON.stringify({
            save_prefs: doSave,
            selected_pics: selectedPics,
            so_priority_only: soPriorityOnly,
            ir_priority_only: irPriorityOnly,
            so_sort_by: soSortBy,
            ir_sort_by: irSortBy,
            so_statuses: soStatusesMap,
            proc_statuses: procStatusesMap,
            so_search: soSearch,
            ir_search: irSearch,
            so_clients: soClients,
            so_vendors: soVendors,
            ir_clients: irClients,
            ir_vendors: irVendors,
            so_status_filter: activeSoCard && activeSoCard !== '__ALL__' ? activeSoCard : '',
            ir_status_filter: activeIrCard && activeIrCard !== '__ALL__' ? activeIrCard : '',
            so_page: soPage,
            so_per_page: soPerPage,
            ir_page: irPage,
            ir_per_page: irPerPage,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data.error || 'Gagal memuat data To Do List (HTTP ' + res.status + ')');
        }
        setPayload(data);
      } catch (err) {
        setError(err && err.message ? err.message : 'Gagal memuat data To Do List');
      } finally {
        setLoading(false);
      }
    }, [
      prefsHydrated,
      shouldSaveOnNextFetch,
      selectedPics,
      soPriorityOnly,
      irPriorityOnly,
      soSortBy,
      irSortBy,
      soStatusesMap,
      procStatusesMap,
      soSearch,
      irSearch,
      soClients,
      soVendors,
      irClients,
      irVendors,
      activeSoCard,
      activeIrCard,
      soPage,
      soPerPage,
      irPage,
      irPerPage,
    ]);

    React.useEffect(() => {
      fetchQueueData();
    }, [fetchQueueData, dataRevision]);

    React.useEffect(() => {
      const onLive = () => fetchQueueData();
      window.addEventListener('serveone:data-changed', onLive);
      return () => window.removeEventListener('serveone:data-changed', onLive);
    }, [fetchQueueData]);

    const handleTogglePic = (picName, singleClickMode) => {
      setSoPage(1);
      setIrPage(1);
      setShouldSaveOnNextFetch(true);
      if (!picName) {
        setSelectedPics([]);
        return;
      }
      if (singleClickMode) {
        if (selectedPics.length === 1 && selectedPics[0] === picName) {
          setSelectedPics([]);
        } else {
          setSelectedPics([picName]);
        }
      } else {
        if (selectedPics.includes(picName)) {
          setSelectedPics(selectedPics.filter((p) => p !== picName));
        } else {
          setSelectedPics([...selectedPics, picName]);
        }
      }
    };

    const updateStatusListConfig = (kind, updater) => {
      if (kind === 'so') setSoPage(1);
      else setIrPage(1);
      setShouldSaveOnNextFetch(true);
      const sourceList = (payload && (kind === 'so' ? payload.so_statuses : payload.proc_statuses)) || [];
      const updatedList = reindexStatusList(updater(sourceList.map((x) => Object.assign({}, x))));
      const nextMap = buildStatusMapFromList(updatedList);
      if (kind === 'so') {
        setSoStatusesMap(nextMap);
      } else {
        setProcStatusesMap(nextMap);
      }
    };

    const handleStatusCheck = (kind, statusName, checked) => {
      updateStatusListConfig(kind, (list) =>
        list.map((item) => (item.status === statusName ? Object.assign({}, item, { checked }) : item))
      );
    };

    const handleStatusPriority = (kind, statusName) => {
      updateStatusListConfig(kind, (list) =>
        list.map((item) => {
          if (item.status !== statusName) return item;
          const nextPrio = !item.priority;
          return Object.assign({}, item, {
            priority: nextPrio,
            checked: nextPrio ? true : item.checked,
            order: nextPrio ? item.order : 999,
          });
        })
      );
    };

    const handleMovePriority = (kind, statusName, direction) => {
      updateStatusListConfig(kind, (list) => {
        const idx = list.findIndex((x) => x.status === statusName);
        if (idx < 0) return list;
        const targetIdx = idx + direction;
        if (targetIdx < 0 || targetIdx >= list.length) return list;
        const copy = [...list];
        const temp = copy[idx];
        copy[idx] = copy[targetIdx];
        copy[targetIdx] = temp;
        return copy.map((item, i) => Object.assign({}, item, { order: i + 1 }));
      });
    };

    const handleReorderPriority = (kind, fromStatus, toStatus) => {
      if (!fromStatus || !toStatus || fromStatus === toStatus) return;
      updateStatusListConfig(kind, (list) => {
        const fromIdx = list.findIndex((x) => x.status === fromStatus);
        const toIdx = list.findIndex((x) => x.status === toStatus);
        if (fromIdx < 0 || toIdx < 0) return list;
        const copy = [...list];
        const targetItem = copy[toIdx];
        const moved = Object.assign({}, copy[fromIdx], {
          priority: Boolean(targetItem.priority),
          checked: targetItem.priority ? true : copy[fromIdx].checked,
        });
        copy.splice(fromIdx, 1);
        copy.splice(toIdx, 0, moved);
        return copy.map((item, i) => Object.assign({}, item, { order: i + 1 }));
      });
    };

    const handleBulkCheck = (kind, mode) => {
      updateStatusListConfig(kind, (list) =>
        list.map((item) => {
          if (mode === 'all') return Object.assign({}, item, { checked: true });
          if (mode === 'priority_only') return Object.assign({}, item, { checked: Boolean(item.priority) });
          return item;
        })
      );
    };

    const handleExportTableCsv = (kind) => {
      const rows = (payload && (kind === 'so' ? payload.so_table?.items : payload.ir_table?.items)) || [];
      if (!rows.length) return;
      const escCsv = (val) => {
        const s = String(val == null ? '' : val);
        return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      };
      let headers, lines;
      if (kind === 'so') {
        headers = [
          'Prioritas',
          'SO Status',
          'Aging (Days)',
          'SO Create Date',
          'Req. Dlv. Date',
          'PIC',
          'SO Number',
          'SO Item',
          'PO Number',
          'Op. Unit Nm.',
          'Vendor',
          'Product ID',
          'Product Name',
          'Specification',
          'Qty',
          'Amount (IDR)',
          'Remarks',
        ];
        lines = [headers.map(escCsv).join(',')];
        rows.forEach((r) => {
          lines.push(
            [
              r.is_priority ? 'Prio #' + r.priority_rank : 'Reguler',
              r.status,
              r.age_days,
              r.create_date,
              r.target_date,
              r.pic,
              r.so_number,
              r.so_item,
              r.secondary_no,
              r.client_or_unit,
              r.vendor_or_mfr,
              r.product_id,
              r.product_name,
              r.specification,
              r.qty,
              r.amount_idr || 0,
              r.remarks,
            ]
              .map(escCsv)
              .join(',')
          );
        });
      } else {
        headers = [
          'Prioritas',
          'Proc. Status',
          'Aging (Days)',
          'Req. Date',
          'Op. Unit / Client',
          'PIC',
          'Req. No.',
          'Origin Nm.',
          'Stock No.',
          'Product ID',
          'Product Name',
          'Specification',
          'Manufacturer',
          'Unit',
          'Remarks',
        ];
        lines = [headers.map(escCsv).join(',')];
        rows.forEach((r) => {
          lines.push(
            [
              r.is_priority ? 'Prio #' + r.priority_rank : 'Reguler',
              r.status,
              r.age_days,
              r.create_date,
              r.client_or_unit,
              r.pic,
              r.so_number,
              r.origin_nm || '',
              r.so_item,
              r.product_id,
              r.product_name,
              r.specification,
              r.vendor_or_mfr,
              r.odr_unit,
              r.remarks,
            ]
              .map(escCsv)
              .join(',')
          );
        });
      }
      const blob = new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      const fname =
        (kind === 'so' ? 'ToDo_Pending_Delivery_' : 'ToDo_Item_Registration_') +
        new Date().toISOString().slice(0, 10) +
        '.csv';
      a.download = fname;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(a.href);
        a.remove();
      }, 200);
    };

    const summary = (payload && payload.summary) || {
      total_followup: 0,
      priority_followup: 0,
      non_priority_followup: 0,
      pending_delivery_count: 0,
      pending_delivery_priority_count: 0,
      pending_delivery_amount_idr: 0,
      item_registration_count: 0,
      item_registration_priority_count: 0,
      overdue_30_count: 0,
    };
    const picOptions = (payload && payload.pic_options) || [];
    const statusBreakdown = (payload && payload.status_breakdown) || [];
    const soStatusCards = statusBreakdown.filter((x) => x.source === 'pending_delivery');
    const irStatusCards = statusBreakdown.filter((x) => x.source === 'item_registration');
    const soStatuses = (payload && payload.so_statuses) || [];
    const procStatuses = (payload && payload.proc_statuses) || [];
    const soTable = (payload && payload.so_table) || { items: [], total: 0, page: 1, per_page: 25, total_pages: 1 };
    const irTable = (payload && payload.ir_table) || { items: [], total: 0, page: 1, per_page: 25, total_pages: 1 };

    const activePicLabel = selectedPics.length === 1 ? selectedPics[0] : 'Semua PIC';

    const handleSelectStatusCard = (kind, cardKey) => {
      if (kind === 'so') {
        setSoPage(1);
        setActiveSoCard((prev) => (prev === cardKey ? null : cardKey));
      } else {
        setIrPage(1);
        setActiveIrCard((prev) => (prev === cardKey ? null : cardKey));
      }
    };

    const renderStatusCards = (kind, cards, activeCard) => {
      const cfgKind = kind === 'so' ? 'so' : 'proc';
      return h(
        'div',
        { className: 'tdl-status-cards-wrap', role: 'group', 'aria-label': kind === 'so' ? 'Status Pending Delivery' : 'Status Item Registration' },
        cards.map((st) => {
          const isActive = activeCard === st.status;
          const isRank1 = Boolean(st.priority && st.priority_rank === 1);
          const isPrio = Boolean(st.priority);
          const ov30 = Number(st.overdue_30_count) || 0;
          const maxAge = Number(st.max_age_days) || 0;
          const isDragging = Boolean(dragInfo && dragInfo.kind === cfgKind && dragInfo.status === st.status);
          const isDragOver = Boolean(dragOverInfo && dragOverInfo.kind === cfgKind && dragOverInfo.status === st.status && !isDragging);
          return h(
            'button',
            {
              key: st.status,
              type: 'button',
              draggable: true,
              className:
                'tdl-status-card' +
                (isRank1 ? ' is-rank-1' : isPrio ? ' is-prio' : ' is-non-prio') +
                (isActive ? ' is-active' : '') +
                (isDragging ? ' is-dragging' : '') +
                (isDragOver ? ' is-drag-over' : ''),
              'aria-pressed': isActive,
              onClick: () => handleSelectStatusCard(kind, st.status),
              onDragStart: (e) => {
                e.dataTransfer.effectAllowed = 'move';
                try { e.dataTransfer.setData('text/plain', st.status); } catch (_) {}
                setDragInfo({ kind: cfgKind, status: st.status });
              },
              onDragOver: (e) => {
                if (!dragInfo || dragInfo.kind !== cfgKind) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = 'move';
                if (!dragOverInfo || dragOverInfo.status !== st.status) {
                  setDragOverInfo({ kind: cfgKind, status: st.status });
                }
              },
              onDragLeave: () => {
                if (dragOverInfo && dragOverInfo.status === st.status) {
                  setDragOverInfo(null);
                }
              },
              onDrop: (e) => {
                e.preventDefault();
                const fromSt = dragInfo && dragInfo.kind === cfgKind ? dragInfo.status : '';
                setDragInfo(null);
                setDragOverInfo(null);
                if (fromSt && fromSt !== st.status) {
                  handleReorderPriority(cfgKind, fromSt, st.status);
                }
              },
              onDragEnd: () => {
                setDragInfo(null);
                setDragOverInfo(null);
              },
              title: st.status + ' (klik: buka tabel · drag & lepas: ubah urutan prioritas)',
            },
            h(
              'div',
              { className: 'tdl-status-card-top' },
              h('span', { className: 'tdl-status-card-name' }, st.status),
              st.priority && st.priority_rank
                ? h(
                    'span',
                    { className: 'tdl-rank-chip ' + (isRank1 ? 'is-rank-1' : 'is-prio') },
                    '#' + st.priority_rank
                  )
                : null
            ),
            h('div', { className: 'tdl-status-card-val' }, fmtInt(st.count)),
            h(
              'div',
              { className: 'tdl-card-urgency' + (ov30 > 0 ? '' : ' is-calm') },
              ov30 > 0
                ? fmtInt(ov30) + ' overdue (>=30d) · max ' + maxAge + 'd'
                : maxAge > 0
                ? 'Tertua ' + maxAge + 'd'
                : 'Klik untuk detail'
            ),
            h(
              'div',
              { className: 'tdl-status-card-sub' },
              kind === 'so' && st.amount_idr > 0 ? fmtCompactIdr(st.amount_idr) : isPrio ? 'Prioritas' : 'Non-prioritas'
            )
          );
        })
      );
    };

    const renderStatusDropdownPopover = (kind, title, list, prioOnly, onTogglePrioOnly) => {
      const popKey = kind + '-status';
      const isOpen = openPopover === popKey;
      const checkedCount = list.filter((x) => x.checked).length;
      const prioCount = list.filter((x) => x.priority).length;
      const label = kind === 'so' ? 'SO Status & Prioritas' : 'Proc. Status & Prioritas';
      return h(
        'div',
        { className: 'tdl-dropdown-anchor' },
        h(
          'button',
          {
            type: 'button',
            className: 'tdl-tool-btn' + (isOpen || prioOnly ? ' is-active' : ''),
            'aria-expanded': isOpen,
            onClick: () => setOpenPopover(isOpen ? null : popKey),
          },
          ICON_LIST(h),
          h('span', null, label),
          h(
            'span',
            { className: 'tdl-count-pill', title: checkedCount + ' status ditampilkan, ' + prioCount + ' prioritas' },
            prioOnly ? 'Prio ' + prioCount : checkedCount + '/' + list.length
          ),
          ICON_CHEVRON(h)
        ),
        isOpen
          ? h(
              React.Fragment,
              null,
              h('div', { className: 'tdl-popover-backdrop', onClick: () => setOpenPopover(null) }),
              h(
                'div',
                { className: 'tdl-popover', role: 'dialog', 'aria-label': title },
                h(
                  'div',
                  { className: 'tdl-popover-head' },
                  h(
                    'div',
                    null,
                    h('div', { className: 'tdl-popover-title' }, title),
                    h(
                      'div',
                      { className: 'tdl-popover-sub' },
                      'Tersimpan & terkunci otomatis (' + checkedCount + ' tampil, ' + prioCount + ' prioritas)'
                    )
                  ),
                  h(
                    'div',
                    { style: { display: 'flex', gap: '0.25rem' } },
                    h(
                      'button',
                      {
                        type: 'button',
                        className: 'tdl-mini-btn',
                        onClick: () => handleBulkCheck(kind, 'all'),
                      },
                      'Pilih Semua'
                    ),
                    h(
                      'button',
                      {
                        type: 'button',
                        className: 'tdl-mini-btn',
                        onClick: () => handleBulkCheck(kind, 'priority_only'),
                      },
                      'Hanya Prioritas'
                    )
                  )
                ),
                h(
                  'div',
                  { className: 'tdl-popover-prio-bar' },
                  h(
                    'label',
                    {
                      htmlFor: 'tdl-prio-only-' + kind,
                      className: 'tdl-clickable',
                      style: {
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '0.5rem',
                        fontSize: '0.75rem',
                        fontWeight: 700,
                      },
                    },
                    h('input', {
                      id: 'tdl-prio-only-' + kind,
                      type: 'checkbox',
                      checked: prioOnly,
                      onChange: (e) => {
                        if (kind === 'so') setSoPage(1);
                        else setIrPage(1);
                        setShouldSaveOnNextFetch(true);
                        onTogglePrioOnly(e.target.checked);
                      },
                    }),
                    h('span', null, 'Sembunyikan item yang tidak prioritas')
                  )
                ),
                h(
                  'div',
                  { className: 'tdl-popover-list' },
                  list.map((st, idx) => {
                    const chkId = 'tdl-pop-chk-' + kind + '-' + idx;
                    const isDragging = Boolean(dragInfo && dragInfo.kind === kind && dragInfo.status === st.status);
                    const isDragOver = Boolean(dragOverInfo && dragOverInfo.kind === kind && dragOverInfo.status === st.status && !isDragging);
                    return h(
                      'div',
                      {
                        key: st.status,
                        draggable: true,
                        className:
                          'tdl-popover-row' +
                          (st.priority ? ' is-priority' : '') +
                          (isDragging ? ' is-dragging' : '') +
                          (isDragOver ? ' is-drag-over' : ''),
                        onDragStart: (e) => {
                          e.dataTransfer.effectAllowed = 'move';
                          try { e.dataTransfer.setData('text/plain', st.status); } catch (_) {}
                          setDragInfo({ kind, status: st.status });
                        },
                        onDragOver: (e) => {
                          if (!dragInfo || dragInfo.kind !== kind) return;
                          e.preventDefault();
                          e.dataTransfer.dropEffect = 'move';
                          if (!dragOverInfo || dragOverInfo.status !== st.status) {
                            setDragOverInfo({ kind, status: st.status });
                          }
                        },
                        onDragLeave: () => {
                          if (dragOverInfo && dragOverInfo.status === st.status) {
                            setDragOverInfo(null);
                          }
                        },
                        onDrop: (e) => {
                          e.preventDefault();
                          const fromSt = dragInfo && dragInfo.kind === kind ? dragInfo.status : '';
                          setDragInfo(null);
                          setDragOverInfo(null);
                          if (fromSt && fromSt !== st.status) {
                            handleReorderPriority(kind, fromSt, st.status);
                          }
                        },
                        onDragEnd: () => {
                          setDragInfo(null);
                          setDragOverInfo(null);
                        },
                      },
                      h(
                        'div',
                        { className: 'tdl-popover-left' },
                        h(
                          'span',
                          { className: 'tdl-drag-grip', title: 'Drag untuk memindah urutan prioritas', 'aria-hidden': 'true' },
                          '⋮⋮'
                        ),
                        h('input', {
                          id: chkId,
                          type: 'checkbox',
                          checked: Boolean(st.checked),
                          onChange: (e) => handleStatusCheck(kind, st.status, e.target.checked),
                          'aria-label': 'Tampilkan status ' + st.status,
                        }),
                        st.priority
                          ? h('span', { className: 'tdl-rank-chip' }, '#' + st.priority_rank)
                          : null,
                        h(
                          'label',
                          {
                            htmlFor: chkId,
                            className: 'tdl-clickable',
                            style: { fontWeight: 600, wordBreak: 'break-word' },
                          },
                          st.status
                        )
                      ),
                      h(
                        'div',
                        { className: 'tdl-popover-right' },
                        h(
                          'span',
                          {
                            style: {
                              fontSize: '0.6875rem',
                              fontWeight: 800,
                              padding: '0.125rem 0.375rem',
                              borderRadius: '0.375rem',
                              backgroundColor: darkMode ? '#374151' : '#E2E8F0',
                            },
                          },
                          fmtInt(st.pic_count)
                        ),
                        h(
                          'button',
                          {
                            type: 'button',
                            className: 'tdl-mini-btn' + (st.priority ? ' is-prio-on' : ''),
                            onClick: () => handleStatusPriority(kind, st.status),
                            'aria-pressed': Boolean(st.priority),
                            title: st.priority ? 'Hapus dari status prioritas' : 'Tandai sebagai status prioritas',
                          },
                          st.priority ? 'Prioritas' : '+ Prio'
                        ),
                        h(
                          'button',
                          {
                            type: 'button',
                            className: 'tdl-mini-btn',
                            disabled: idx === 0,
                            onClick: () => handleMovePriority(kind, st.status, -1),
                            'aria-label': 'Naikkan urutan ' + st.status,
                            title: 'Naikkan urutan prioritas',
                          },
                          '↑'
                        ),
                        h(
                          'button',
                          {
                            type: 'button',
                            className: 'tdl-mini-btn',
                            disabled: idx === list.length - 1,
                            onClick: () => handleMovePriority(kind, st.status, 1),
                            'aria-label': 'Turunkan urutan ' + st.status,
                            title: 'Turunkan urutan prioritas',
                          },
                          '↓'
                        )
                      )
                    );
                  })
                )
              )
            )
          : null
      );
    };

    const renderTableMultiFilterPopover = (popKey, label, options, selected, setSelected, kind) => {
      const isOpen = openPopover === popKey;
      const list = Array.isArray(options) ? options : [];
      const q = multiFilterSearch.trim().toLowerCase();
      const filtered = q ? list.filter((x) => x.toLowerCase().includes(q)) : list;
      const shown = filtered.slice(0, 120);
      const toggleVal = (val) => {
        if (kind === 'so') setSoPage(1);
        else setIrPage(1);
        setSelected((prev) => (prev.includes(val) ? prev.filter((x) => x !== val) : [...prev, val]));
      };
      return h(
        'div',
        { className: 'tdl-dropdown-anchor' },
        h(
          'button',
          {
            type: 'button',
            className: 'tdl-tool-btn' + (isOpen || selected.length > 0 ? ' is-active' : ''),
            'aria-expanded': isOpen,
            onClick: () => {
              setMultiFilterSearch('');
              setOpenPopover(isOpen ? null : popKey);
            },
          },
          ICON_FILTER(h),
          h('span', null, label),
          selected.length > 0 ? h('span', { className: 'tdl-count-pill' }, selected.length) : null,
          ICON_CHEVRON(h)
        ),
        isOpen
          ? h(
              React.Fragment,
              null,
              h('div', { className: 'tdl-popover-backdrop', onClick: () => setOpenPopover(null) }),
              h(
                'div',
                { className: 'tdl-popover is-narrow', role: 'dialog', 'aria-label': 'Filter ' + label },
                h(
                  'div',
                  { className: 'tdl-popover-head', style: { flexDirection: 'column', alignItems: 'stretch', gap: '0.375rem' } },
                  h('input', {
                    type: 'search',
                    className: 'tdl-input',
                    style: { width: '100%', height: '32px' },
                    placeholder: 'Cari ' + label + '...',
                    value: multiFilterSearch,
                    autoFocus: true,
                    onChange: (e) => setMultiFilterSearch(e.target.value),
                  }),
                  h(
                    'div',
                    { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.6875rem' } },
                    h('span', null, selected.length + ' dipilih dari ' + fmtInt(list.length)),
                    selected.length > 0
                      ? h(
                          'button',
                          {
                            type: 'button',
                            className: 'tdl-mini-btn',
                            onClick: () => {
                              if (kind === 'so') setSoPage(1);
                              else setIrPage(1);
                              setSelected([]);
                            },
                          },
                          'Clear'
                        )
                      : null
                  )
                ),
                h(
                  'div',
                  { className: 'tdl-popover-list', style: { maxHeight: '240px' } },
                  shown.length === 0
                    ? h('div', { style: { padding: '0.75rem', fontSize: '0.75rem', textAlign: 'center', color: '#6B7280' } }, 'Tidak ada opsi cocok')
                    : shown.map((opt) =>
                        h(
                          'label',
                          { key: opt, className: 'tdl-popover-row tdl-clickable', style: { justifyContent: 'flex-start', gap: '0.5rem' } },
                          h('input', {
                            type: 'checkbox',
                            checked: selected.includes(opt),
                            onChange: () => toggleVal(opt),
                          }),
                          h('span', { style: { wordBreak: 'break-word', fontWeight: 500 } }, opt)
                        )
                      )
                )
              )
            )
          : null
      );
    };

    const renderSearchDropdownPopover = (kind, draftVal, setDraftVal, appliedVal, setAppliedVal, placeholder) => {
      const popKey = kind + '-search';
      const isOpen = openPopover === popKey;
      return h(
        'div',
        { className: 'tdl-dropdown-anchor' },
        h(
          'button',
          {
            type: 'button',
            className: 'tdl-tool-btn' + (isOpen || appliedVal ? ' is-active' : ''),
            'aria-expanded': isOpen,
            onClick: () => setOpenPopover(isOpen ? null : popKey),
          },
          ICON_SEARCH(h),
          h('span', null, appliedVal ? 'Search: ' + appliedVal.slice(0, 14) : 'Search'),
          ICON_CHEVRON(h)
        ),
        isOpen
          ? h(
              React.Fragment,
              null,
              h('div', { className: 'tdl-popover-backdrop', onClick: () => setOpenPopover(null) }),
              h(
                'div',
                { className: 'tdl-popover is-narrow', role: 'dialog', 'aria-label': 'Search ' + kind },
                h(
                  'form',
                  {
                    onSubmit: (e) => {
                      e.preventDefault();
                      if (kind === 'so') setSoPage(1);
                      else setIrPage(1);
                      setAppliedVal(draftVal.trim());
                      setOpenPopover(null);
                    },
                    style: { padding: '0.875rem', display: 'flex', flexDirection: 'column', gap: '0.625rem' },
                  },
                  h(
                    'label',
                    { htmlFor: 'tdl-search-box-' + kind, className: 'tdl-popover-title' },
                    kind === 'so' ? 'Cari di Pending Delivery' : 'Cari di Item Registration'
                  ),
                  h('input', {
                    id: 'tdl-search-box-' + kind,
                    type: 'search',
                    className: 'tdl-input',
                    style: { width: '100%' },
                    placeholder,
                    value: draftVal,
                    autoFocus: true,
                    onChange: (e) => setDraftVal(e.target.value),
                  }),
                  h(
                    'div',
                    { style: { display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' } },
                    h(
                      'button',
                      {
                        type: 'button',
                        className: 'tdl-mini-btn',
                        onClick: () => {
                          setDraftVal('');
                          setAppliedVal('');
                          if (kind === 'so') setSoPage(1);
                          else setIrPage(1);
                          setOpenPopover(null);
                        },
                      },
                      'Clear'
                    ),
                    h(
                      'button',
                      {
                        type: 'submit',
                        className: 'tdl-tool-btn is-primary-blue',
                        style: { minHeight: '32px', padding: '0.25rem 0.75rem' },
                      },
                      'Search'
                    )
                  )
                )
              )
            )
          : null
      );
    };

    const renderFilterDrawer = (kind, sortVal, setSortVal, prioOnly, setPrioOnly) => {
      return h(
        'div',
        { className: 'tdl-filter-drawer' },
        h(
          'div',
          { className: 'tdl-filter-field' },
          h(
            'label',
            { htmlFor: 'tdl-sort-' + kind, className: 'tdl-filter-label' },
            '↕ Urutan Prioritas & Aging'
          ),
          h(
            'select',
            {
              id: 'tdl-sort-' + kind,
              className: 'tdl-select',
              value: sortVal,
              onChange: (e) => {
                if (kind === 'so') setSoPage(1);
                else setIrPage(1);
                setShouldSaveOnNextFetch(true);
                setSortVal(e.target.value);
              },
            },
            h('option', { value: 'priority_oldest' }, 'Prioritas Status + Terlama Menunggu'),
            h('option', { value: 'oldest' }, 'Terlama Menunggu (Aging Tertinggi)'),
            kind === 'so' ? h('option', { value: 'amount_desc' }, 'Prioritas + Nilai IDR Terbesar') : null,
            h('option', { value: 'newest' }, 'Terbaru Dibuat')
          )
        ),
        h(
          'div',
          { className: 'tdl-filter-field' },
          h(
            'label',
            { htmlFor: 'tdl-pic-sel-' + kind, className: 'tdl-filter-label' },
            'PIC Follow-Up (Otomatis Tersimpan)'
          ),
          h(
            'select',
            {
              id: 'tdl-pic-sel-' + kind,
              className: 'tdl-select',
              value: selectedPics.length === 1 ? selectedPics[0] : '',
              onChange: (e) => handleTogglePic(e.target.value, true),
            },
            h('option', { value: '' }, 'Semua PIC (' + fmtInt(summary.total_followup) + ')'),
            picOptions.map((p) =>
              h(
                'option',
                { key: p.pic, value: p.pic },
                p.pic + ' (' + fmtInt(kind === 'so' ? p.so_count : p.ir_count) + ')'
              )
            )
          )
        ),
        h(
          'div',
          { className: 'tdl-filter-field', style: { justifyContent: 'center', minHeight: '36px' } },
          h(
            'label',
            {
              htmlFor: 'tdl-drawer-prio-' + kind,
              className: 'tdl-clickable',
              style: { display: 'inline-flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.75rem', fontWeight: 700 },
            },
            h('input', {
              id: 'tdl-drawer-prio-' + kind,
              type: 'checkbox',
              checked: prioOnly,
              onChange: (e) => {
                if (kind === 'so') setSoPage(1);
                else setIrPage(1);
                setShouldSaveOnNextFetch(true);
                setPrioOnly(e.target.checked);
              },
            }),
            h('span', null, 'Hanya tampilkan item prioritas')
          )
        )
      );
    };

    const renderPager = (kind, tableObj, pageVal, setPageVal, perPageVal, setPerPageVal) => {
      const totalRows = Number(tableObj.total || 0);
      const totalPages = Math.max(1, Number(tableObj.total_pages || 1));
      const fromRow = totalRows === 0 ? 0 : (pageVal - 1) * perPageVal + 1;
      const toRow = Math.min(totalRows, pageVal * perPageVal);
      return h(
        'div',
        { className: 'tdl-pager' },
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' } },
          h('span', { style: { fontSize: '0.875rem' } }, 'Showing ' + fmtInt(fromRow) + '-' + fmtInt(toRow) + ' of ' + fmtInt(totalRows)),
          h(
            'label',
            { htmlFor: 'tdl-rows-' + kind, style: { display: 'inline-flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem' } },
            'Rows',
            h(
              'select',
              {
                id: 'tdl-rows-' + kind,
                className: 'tdl-select',
                style: { height: '28px', padding: '0.125rem 0.5rem', fontSize: '0.75rem', borderRadius: '0.5rem' },
                value: perPageVal,
                onChange: (e) => {
                  setPageVal(1);
                  setPerPageVal(Number(e.target.value) || 25);
                },
              },
              h('option', { value: 10 }, '10'),
              h('option', { value: 25 }, '25'),
              h('option', { value: 50 }, '50'),
              h('option', { value: 100 }, '100'),
              h('option', { value: 500 }, '500')
            )
          )
        ),
        h(
          'div',
          { style: { display: 'flex', alignItems: 'center', gap: '0.25rem' } },
          h(
            'button',
            {
              type: 'button',
              className: 'tdl-pager-btn',
              disabled: pageVal <= 1,
              'aria-label': 'Halaman sebelumnya ' + kind,
              onClick: () => setPageVal(Math.max(1, pageVal - 1)),
            },
            ICON_CHEVRON_LEFT(h)
          ),
          h('span', { className: 'tdl-pager-pill' }, pageVal + '/' + totalPages),
          h(
            'button',
            {
              type: 'button',
              className: 'tdl-pager-btn',
              disabled: pageVal >= totalPages,
              'aria-label': 'Halaman berikutnya ' + kind,
              onClick: () => setPageVal(Math.min(totalPages, pageVal + 1)),
            },
            ICON_CHEVRON_RIGHT(h)
          )
        )
      );
    };

    const activeFilterCountSo =
      (selectedPics.length ? 1 : 0) +
      (soPriorityOnly ? 1 : 0) +
      (soSortBy !== 'priority_oldest' ? 1 : 0) +
      (soStatuses.some((x) => !x.checked) ? 1 : 0);

    const activeFilterCountIr =
      (selectedPics.length ? 1 : 0) +
      (irPriorityOnly ? 1 : 0) +
      (irSortBy !== 'priority_oldest' ? 1 : 0);

    return h(
      'div',
      { className: 'tdl-page' + (darkMode ? ' is-dark' : '') },

      // 1. Top 3-Card Summary Bar (Pending Delivery, Pending Item Registration, 1 PIC)
      h(
        'div',
        { className: 'tdl-summary-grid', role: 'region', 'aria-label': 'Ringkasan To Do List' },
        h(
          'button',
          {
            type: 'button',
            className: 'tdl-summary-card is-clickable' + (activeSoCard === '__ALL__' ? ' is-active' : ''),
            'aria-pressed': activeSoCard === '__ALL__',
            title: 'Klik untuk menampilkan semua tabel Pending Delivery (Show All)',
            onClick: () => handleSelectStatusCard('so', '__ALL__'),
          },
          h(
            'div',
            { style: { minWidth: 0 } },
            h('div', { className: 'tdl-summary-label' }, 'Pending Delivery'),
            h('div', { className: 'tdl-summary-val' }, fmtInt(summary.pending_delivery_count)),
            h(
              'div',
              { className: 'tdl-summary-sub' },
              fmtInt(summary.pending_delivery_priority_count) +
                ' prioritas · ' +
                fmtInt(summary.pending_delivery_overdue_30 || 0) +
                ' overdue (>=30d) · ' +
                fmtCompactIdr(summary.pending_delivery_amount_idr)
            )
          ),
          h('div', { className: 'tdl-summary-icon' }, ICON_BOX(h))
        ),
        h(
          'button',
          {
            type: 'button',
            className: 'tdl-summary-card is-clickable' + (activeIrCard === '__ALL__' ? ' is-active' : ''),
            'aria-pressed': activeIrCard === '__ALL__',
            title: 'Klik untuk menampilkan semua tabel Item Registration (Show All)',
            onClick: () => handleSelectStatusCard('ir', '__ALL__'),
          },
          h(
            'div',
            { style: { minWidth: 0 } },
            h('div', { className: 'tdl-summary-label' }, 'Pending Item Registration'),
            h('div', { className: 'tdl-summary-val' }, fmtInt(summary.item_registration_count)),
            h(
              'div',
              { className: 'tdl-summary-sub' },
              fmtInt(summary.item_registration_priority_count) +
                ' prioritas · ' +
                fmtInt(summary.item_registration_overdue_30 || 0) +
                ' overdue (>=30d)'
            )
          ),
          h('div', { className: 'tdl-summary-icon' }, ICON_CLIPBOARD(h))
        ),
        h(
          'div',
          { className: 'tdl-summary-card' },
          h(
            'div',
            { style: { minWidth: 0, display: 'flex', flexDirection: 'column' } },
            h('div', { className: 'tdl-summary-label' }, 'PIC'),
            h('div', { className: 'tdl-summary-val' }, activePicLabel),
            h(
              'div',
              { className: 'tdl-summary-sub' },
              'Total ' +
                fmtInt(summary.total_followup) +
                ' follow-up · ' +
                fmtInt(summary.priority_followup || 0) +
                ' prioritas'
            )
          ),
          h('div', { className: 'tdl-summary-icon' }, ICON_USER(h))
        )
      ),

      // 1b. Centralized 1-Row Filter Bar below KPI
      h(
        'div',
        { className: 'tdl-central-filter-bar', role: 'region', 'aria-label': 'Filter Terpusat To Do List' },
        h(
          'div',
          { className: 'tdl-central-field' },
          h('label', { htmlFor: 'tdl-central-pic', className: 'tdl-central-label' }, 'PIC'),
          h(
            'select',
            {
              id: 'tdl-central-pic',
              className: 'tdl-select',
              value: selectedPics.length === 1 ? selectedPics[0] : '',
              onChange: (e) => {
                setActiveSoCard(null);
                setActiveIrCard(null);
                handleTogglePic(e.target.value, true);
              },
            },
            h('option', { value: '' }, 'Semua PIC (' + fmtInt(summary.total_followup) + ')'),
            picOptions.map((p) =>
              h('option', { key: p.pic, value: p.pic }, p.pic + ' (' + fmtInt(p.count) + ')')
            )
          )
        ),
        h('div', { className: 'tdl-central-divider', 'aria-hidden': 'true' }),
        renderStatusDropdownPopover(
          'so',
          'Checklist SO Status & Urutan Prioritas',
          soStatuses,
          soPriorityOnly,
          setSoPriorityOnly
        ),
        renderStatusDropdownPopover(
          'proc',
          'Checklist Proc. Status & Urutan Prioritas',
          procStatuses,
          irPriorityOnly,
          setIrPriorityOnly
        ),
        h('div', { className: 'tdl-central-divider', 'aria-hidden': 'true' }),
        h(
          'div',
          { className: 'tdl-central-field' },
          h('label', { htmlFor: 'tdl-central-sort', className: 'tdl-central-label' }, '↕ Urutan'),
          h(
            'select',
            {
              id: 'tdl-central-sort',
              className: 'tdl-select',
              value: soSortBy,
              onChange: (e) => {
                const v = e.target.value;
                setSoPage(1);
                setIrPage(1);
                setShouldSaveOnNextFetch(true);
                setSoSortBy(v);
                setIrSortBy(v === 'amount_desc' ? 'priority_oldest' : v);
              },
            },
            h('option', { value: 'priority_oldest' }, 'Prioritas + Terlama'),
            h('option', { value: 'oldest' }, 'Aging Terlama'),
            h('option', { value: 'amount_desc' }, 'Prioritas + Nilai IDR'),
            h('option', { value: 'newest' }, 'Terbaru Dibuat')
          )
        ),
        h(
          'label',
          {
            htmlFor: 'tdl-central-prio-only',
            className: 'tdl-prio-toggle-pill tdl-clickable' + (soPriorityOnly && irPriorityOnly ? ' is-on' : ''),
            title: 'Sembunyikan semua status yang tidak ditandai prioritas',
          },
          h('input', {
            id: 'tdl-central-prio-only',
            type: 'checkbox',
            checked: Boolean(soPriorityOnly && irPriorityOnly),
            onChange: (e) => {
              const checked = e.target.checked;
              setSoPage(1);
              setIrPage(1);
              setShouldSaveOnNextFetch(true);
              setSoPriorityOnly(checked);
              setIrPriorityOnly(checked);
            },
          }),
          h('span', null, 'Hanya Prioritas')
        )
      ),

      error
        ? h(
            'div',
            { className: 'tdl-table-card', style: { padding: '1rem 1.25rem' }, role: 'alert' },
            h(
              'div',
              { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem' } },
              h('span', { style: { fontWeight: 700, color: '#DC2626' } }, error),
              h(
                'button',
                { type: 'button', className: 'tdl-tool-btn is-primary-blue', onClick: fetchQueueData },
                'Muat Ulang'
              )
            )
          )
        : null,

      // 2. SECTION 1: Pending Delivery Follow-Up
      h(
        'section',
        { className: 'tdl-table-card', 'aria-label': 'Pending Delivery Follow-Up' },
        h(
          'div',
          { className: 'tdl-card-header' },
          h(
            'div',
            { className: 'tdl-card-title-group' },
            ICON_BOX(h),
            h('h2', { className: 'tdl-card-title' }, 'Pending Delivery'),
            h(
              'span',
              { className: 'tdl-card-count' },
              '(' +
                fmtInt(summary.pending_delivery_count) +
                ' records · ' +
                fmtInt(summary.pending_delivery_priority_count) +
                ' prioritas · ' +
                fmtCompactIdr(summary.pending_delivery_amount_idr) +
                ')'
            )
          ),
        ),
        renderStatusCards('so', soStatusCards, activeSoCard),
        h(
          'div',
          { className: 'tdl-accordion' + (soExpanded ? ' is-open' : '') + (soSettled ? ' is-settled' : '') },
          h(
            'div',
            { className: 'tdl-accordion-inner' },
            soMountedCard
              ? h(
                  React.Fragment,
                  null,
                  h(
                    'div',
                    { className: 'tdl-active-table-bar' },
                    h(
                      'span',
                      null,
                      'Menampilkan: ' +
                        (soMountedCard === '__ALL__' ? 'Show All Pending Delivery' : soMountedCard) +
                        ' (' +
                        fmtInt(soTable.total) +
                        ' records)'
                    ),
                    h(
                      'div',
                      { style: { display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' } },
                      soClients.length > 0 || soVendors.length > 0 || soSearch
                        ? h(
                            'button',
                            {
                              type: 'button',
                              className: 'tdl-mini-btn',
                              onClick: () => {
                                setSoClients([]);
                                setSoVendors([]);
                                setSoSearchDraft('');
                                setSoSearch('');
                                setSoPage(1);
                              },
                            },
                            'Reset Filter'
                          )
                        : null,
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-mini-btn',
                          onClick: () => resetColWidths('so'),
                          title: 'Kembalikan lebar kolom default',
                        },
                        'Reset Lebar Kolom'
                      ),
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-mini-btn',
                          onClick: () => setActiveSoCard(null),
                        },
                        'Tutup Tabel'
                      ),
                      renderSearchDropdownPopover(
                        'so',
                        soSearchDraft,
                        setSoSearchDraft,
                        soSearch,
                        setSoSearch,
                        'SO No., PO No., Produk, Vendor...'
                      ),
                      renderTableMultiFilterPopover(
                        'so-client',
                        'Client',
                        soTable.client_options || [],
                        soClients,
                        setSoClients,
                        'so'
                      ),
                      renderTableMultiFilterPopover(
                        'so-vendor',
                        'Vendor',
                        soTable.vendor_options || [],
                        soVendors,
                        setSoVendors,
                        'so'
                      ),
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-tool-btn is-primary-blue',
                          disabled: !soTable.items.length,
                          onClick: () => handleExportTableCsv('so'),
                        },
                        ICON_DOWNLOAD(h),
                        h('span', null, 'Download Excel')
                      )
                    )
                  ),
                  h(
                    'div',
                    { ref: soTableWrapRef, className: 'tdl-table-wrap' + (loading ? ' is-loading' : '') },
              h(
                'table',
                { className: 'tdl-table is-resizable' },
                h(
                  'colgroup',
                  null,
                  Object.keys(DEFAULT_SO_COL_WIDTHS).map((colKey) =>
                    h('col', { key: colKey, style: { width: (colWidths.so[colKey] || DEFAULT_SO_COL_WIDTHS[colKey]) + 'px' } })
                  )
                ),
                h(
                  'thead',
                  null,
                  h(
                    'tr',
                    null,
                    [
                      ['No', (r, i) => (soTable.page - 1) * soTable.per_page + i + 1],
                      ['SO Status & Prioritas', (r) => r.status],
                      ['Aging', (r) => r.age_days + 'd'],
                      ['SO Create Date', (r) => fmtDateShort(r.create_date)],
                      ['Req. Dlv. Date', (r) => fmtDateShort(r.target_date)],
                      ['PIC', (r) => r.pic],
                      ['SO No.', (r) => r.so_number],
                      ['Item', (r) => r.so_item],
                      ['PO No.', (r) => r.secondary_no],
                      ['Op. Unit Nm.', (r) => r.client_or_unit],
                      ['Vendor', (r) => r.vendor_or_mfr],
                      ['Prod. ID', (r) => r.product_id],
                      ['Product Name', (r) => r.product_name],
                      ['Specification', (r) => r.specification],
                      ['Qty', (r) => r.qty],
                      ['Amount (IDR)', (r) => r.amount_idr],
                      ['Remarks', (r) => r.remarks],
                    ].map(([label, getter]) => {
                      const colW = colWidths.so[label] || DEFAULT_SO_COL_WIDTHS[label] || 120;
                      return h(
                        'th',
                        {
                          key: label,
                          className: 'tdl-th-copyable',
                          style: { width: colW + 'px' },
                          title: label + ' (Ctrl+click: copy kolom · drag tepi kanan: ubah lebar)',
                          onClick: (e) => {
                            if (resizingRef.current) return;
                            if (e.ctrlKey || e.metaKey || e.shiftKey) {
                              e.preventDefault();
                              copyColumnValues(soTable.items, label, getter, showToast);
                            }
                          },
                        },
                        label,
                        h('div', {
                          className: 'tdl-col-resizer',
                          title: 'Drag untuk memperlebar atau memperkecil kolom',
                          onMouseDown: (e) => startColResize(e, 'so', label, colW),
                          onClick: (e) => e.stopPropagation(),
                        })
                      );
                    })
                  )
                ),
            h(
              'tbody',
              null,
              loading && !payload
                ? h(
                    'tr',
                    null,
                    h(
                      'td',
                      { colSpan: 17, className: 'tdl-empty-cell' },
                      'Memuat data Pending Delivery follow-up...'
                    )
                  )
                : soTable.items.length === 0
                ? h(
                    'tr',
                    null,
                    h(
                      'td',
                      { colSpan: 17, className: 'tdl-empty-cell' },
                      'Tidak ada data Pending Delivery yang cocok dengan filter status/prioritas saat ini.'
                    )
                  )
                : soTable.items.map((item, idx) => {
                    const rowNo = (soTable.page - 1) * soTable.per_page + idx + 1;
                    const ageClass =
                      item.age_days >= 90 ? 'is-crit' : item.age_days >= 30 ? 'is-warn' : 'is-ok';
                    return h(
                      'tr',
                      { key: item.uid },
                      h('td', { style: { textAlign: 'center', fontWeight: 700 } }, rowNo),
                      h(
                        'td',
                        null,
                        h(
                          'div',
                          { style: { display: 'inline-flex', alignItems: 'center', gap: '0.375rem' } },
                          item.is_priority
                            ? h('span', { className: 'tdl-rank-chip' }, '#' + item.priority_rank)
                            : null,
                          h(
                            'span',
                            { className: 'tdl-status-chip' + (item.is_priority ? ' is-prio' : '') },
                            item.status
                          )
                        )
                      ),
                      h(
                        'td',
                        { style: { textAlign: 'center' } },
                        h('span', { className: 'tdl-age-chip ' + ageClass }, item.age_days + 'd')
                      ),
                      h('td', { style: { textAlign: 'center', whiteSpace: 'nowrap' } }, fmtDateShort(item.create_date)),
                      h('td', { style: { textAlign: 'center', whiteSpace: 'nowrap' } }, fmtDateShort(item.target_date)),
                      h('td', { style: { textAlign: 'center', fontWeight: 800 } }, item.pic || '-'),
                      h('td', { style: { fontFamily: 'monospace', fontWeight: 700, whiteSpace: 'nowrap' } }, item.so_number || '-'),
                      h('td', { style: { textAlign: 'center', fontFamily: 'monospace' } }, item.so_item || '-'),
                      h('td', { style: { fontFamily: 'monospace', whiteSpace: 'nowrap' } }, item.secondary_no || '-'),
                      h('td', { style: { minWidth: '150px' } }, item.client_or_unit || '-'),
                      h('td', { style: { minWidth: '150px' } }, item.vendor_or_mfr || '-'),
                      h('td', { style: { fontFamily: 'monospace', textAlign: 'center' } }, item.product_id || '-'),
                      h('td', { style: { minWidth: '180px', fontWeight: 600 } }, item.product_name || '-'),
                      h('td', { style: { minWidth: '180px', maxWidth: '280px' } }, item.specification || '-'),
                      h('td', { style: { textAlign: 'right', fontWeight: 700 } }, fmtInt(item.qty)),
                      h('td', { style: { textAlign: 'right', fontWeight: 700, whiteSpace: 'nowrap' } }, fmtFullIdr(item.amount_idr)),
                      h('td', { style: { minWidth: '140px', maxWidth: '220px' } }, item.remarks || '-')
                    );
                  })
            )
          )
        ),
        renderPager('so', soTable, soPage, setSoPage, soPerPage, setSoPerPage)
                )
              : null
          )
        )
      ),

      // 3. TABLE 2: Item Registration Follow-Up
      h(
        'section',
        { className: 'tdl-table-card', 'aria-label': 'Item Registration Follow-Up' },
        h(
          'div',
          { className: 'tdl-card-header' },
          h(
            'div',
            { className: 'tdl-card-title-group' },
            ICON_CLIPBOARD(h),
            h('h2', { className: 'tdl-card-title' }, 'Item Registration'),
            h(
              'span',
              { className: 'tdl-card-count' },
              '(' +
                fmtInt(summary.item_registration_count) +
                ' records · ' +
                fmtInt(summary.item_registration_priority_count) +
                ' prioritas)'
            )
          ),
        ),
        renderStatusCards('ir', irStatusCards, activeIrCard),
        h(
          'div',
          { className: 'tdl-accordion' + (irExpanded ? ' is-open' : '') + (irSettled ? ' is-settled' : '') },
          h(
            'div',
            { className: 'tdl-accordion-inner' },
            irMountedCard
              ? h(
                  React.Fragment,
                  null,
                  h(
                    'div',
                    { className: 'tdl-active-table-bar' },
                    h(
                      'span',
                      null,
                      'Menampilkan: ' +
                        (irMountedCard === '__ALL__' ? 'Show All Item Registration' : irMountedCard) +
                        ' (' +
                        fmtInt(irTable.total) +
                        ' records)'
                    ),
                    h(
                      'div',
                      { style: { display: 'inline-flex', flexWrap: 'wrap', alignItems: 'center', gap: '0.5rem' } },
                      irClients.length > 0 || irVendors.length > 0 || irSearch
                        ? h(
                            'button',
                            {
                              type: 'button',
                              className: 'tdl-mini-btn',
                              onClick: () => {
                                setIrClients([]);
                                setIrVendors([]);
                                setIrSearchDraft('');
                                setIrSearch('');
                                setIrPage(1);
                              },
                            },
                            'Reset Filter'
                          )
                        : null,
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-mini-btn',
                          onClick: () => resetColWidths('ir'),
                          title: 'Kembalikan lebar kolom default',
                        },
                        'Reset Lebar Kolom'
                      ),
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-mini-btn',
                          onClick: () => setActiveIrCard(null),
                        },
                        'Tutup Tabel'
                      ),
                      renderSearchDropdownPopover(
                        'ir',
                        irSearchDraft,
                        setIrSearchDraft,
                        irSearch,
                        setIrSearch,
                        'Req. No., Stock No., Produk, Manufacturer...'
                      ),
                      renderTableMultiFilterPopover(
                        'ir-client',
                        'Client',
                        irTable.client_options || [],
                        irClients,
                        setIrClients,
                        'ir'
                      ),
                      renderTableMultiFilterPopover(
                        'ir-vendor',
                        'Vendor / Mfr.',
                        irTable.vendor_options || [],
                        irVendors,
                        setIrVendors,
                        'ir'
                      ),
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'tdl-tool-btn is-primary-blue',
                          disabled: !irTable.items.length,
                          onClick: () => handleExportTableCsv('ir'),
                        },
                        ICON_DOWNLOAD(h),
                        h('span', null, 'Download Excel')
                      )
                    )
                  ),
                  h(
                    'div',
                    { ref: irTableWrapRef, className: 'tdl-table-wrap' + (loading ? ' is-loading' : '') },
              h(
                'table',
                { className: 'tdl-table is-resizable' },
                h(
                  'colgroup',
                  null,
                  Object.keys(DEFAULT_IR_COL_WIDTHS).map((colKey) =>
                    h('col', { key: colKey, style: { width: (colWidths.ir[colKey] || DEFAULT_IR_COL_WIDTHS[colKey]) + 'px' } })
                  )
                ),
                h(
                  'thead',
                  null,
                  h(
                    'tr',
                    null,
                    [
                      ['No', (r, i) => (irTable.page - 1) * irTable.per_page + i + 1],
                      ['Proc. Status & Prioritas', (r) => r.status],
                      ['Aging', (r) => r.age_days + 'd'],
                      ['Req. Date', (r) => fmtDateShort(r.create_date)],
                      ['Op. Unit / Client', (r) => r.client_or_unit],
                      ['PIC', (r) => r.pic],
                      ['Req. No.', (r) => r.so_number],
                      ['Origin Nm.', (r) => r.origin_nm || ''],
                      ['Stock No.', (r) => r.so_item],
                      ['Prod. ID', (r) => r.product_id],
                      ['Prod. Nm.', (r) => r.product_name],
                      ['Spec.', (r) => r.specification],
                      ['Mfr. Nm.', (r) => r.vendor_or_mfr],
                      ['Unit', (r) => r.odr_unit],
                      ['Remarks', (r) => r.remarks],
                    ].map(([label, getter]) => {
                      const colW = colWidths.ir[label] || DEFAULT_IR_COL_WIDTHS[label] || 120;
                      return h(
                        'th',
                        {
                          key: label,
                          className: 'tdl-th-copyable',
                          style: { width: colW + 'px' },
                          title: label + ' (Ctrl+click: copy kolom · drag tepi kanan: ubah lebar)',
                          onClick: (e) => {
                            if (resizingRef.current) return;
                            if (e.ctrlKey || e.metaKey || e.shiftKey) {
                              e.preventDefault();
                              copyColumnValues(irTable.items, label, getter, showToast);
                            }
                          },
                        },
                        label,
                        h('div', {
                          className: 'tdl-col-resizer',
                          title: 'Drag untuk memperlebar atau memperkecil kolom',
                          onMouseDown: (e) => startColResize(e, 'ir', label, colW),
                          onClick: (e) => e.stopPropagation(),
                        })
                      );
                    })
                  )
                ),
            h(
              'tbody',
              null,
              loading && !payload
                ? h(
                    'tr',
                    null,
                    h(
                      'td',
                      { colSpan: 15, className: 'tdl-empty-cell' },
                      'Memuat data Item Registration follow-up...'
                    )
                  )
                : irTable.items.length === 0
                ? h(
                    'tr',
                    null,
                    h(
                      'td',
                      { colSpan: 15, className: 'tdl-empty-cell' },
                      'Tidak ada data Item Registration yang cocok dengan filter status/prioritas saat ini.'
                    )
                  )
                : irTable.items.map((item, idx) => {
                    const rowNo = (irTable.page - 1) * irTable.per_page + idx + 1;
                    const ageClass =
                      item.age_days >= 90 ? 'is-crit' : item.age_days >= 30 ? 'is-warn' : 'is-ok';
                    return h(
                      'tr',
                      { key: item.uid },
                      h('td', { style: { textAlign: 'center', fontWeight: 700 } }, rowNo),
                      h(
                        'td',
                        null,
                        h(
                          'div',
                          { style: { display: 'inline-flex', alignItems: 'center', gap: '0.375rem' } },
                          item.is_priority
                            ? h('span', { className: 'tdl-rank-chip' }, '#' + item.priority_rank)
                            : null,
                          h(
                            'span',
                            { className: 'tdl-status-chip' + (item.is_priority ? ' is-prio' : '') },
                            item.status
                          )
                        )
                      ),
                      h(
                        'td',
                        { style: { textAlign: 'center' } },
                        h('span', { className: 'tdl-age-chip ' + ageClass }, item.age_days + 'd')
                      ),
                      h('td', { style: { textAlign: 'center', whiteSpace: 'nowrap' } }, fmtDateShort(item.create_date)),
                      h('td', { style: { minWidth: '160px' } }, item.client_or_unit || '-'),
                      h('td', { style: { textAlign: 'center', fontWeight: 800 } }, item.pic || '-'),
                      h('td', { style: { fontFamily: 'monospace', fontWeight: 700, whiteSpace: 'nowrap' } }, item.so_number || '-'),
                      h(
                        'td',
                        {
                          style: String(item.origin_nm || '').trim().toLowerCase().includes('south korea')
                            ? { color: '#2563eb', fontWeight: 700, whiteSpace: 'nowrap' }
                            : { whiteSpace: 'nowrap' },
                          title: item.origin_nm || '',
                        },
                        item.origin_nm || '-'
                      ),
                      h('td', { style: { fontFamily: 'monospace', textAlign: 'center' } }, item.so_item || '-'),
                      h('td', { style: { fontFamily: 'monospace', textAlign: 'center' } }, item.product_id || '-'),
                      h('td', { style: { minWidth: '180px', fontWeight: 600 } }, item.product_name || '-'),
                      h('td', { style: { minWidth: '180px', maxWidth: '280px' } }, item.specification || '-'),
                      h('td', { style: { minWidth: '140px' } }, item.vendor_or_mfr || '-'),
                      h('td', { style: { textAlign: 'center' } }, item.odr_unit || '-'),
                      h('td', { style: { minWidth: '140px', maxWidth: '220px' } }, item.remarks || '-')
                    );
                  })
            )
          )
        ),
        renderPager('ir', irTable, irPage, setIrPage, irPerPage, setIrPerPage)
                )
              : null
          )
        )
      ),
      toastMsg ? h('div', { className: 'tdl-toast', role: 'status', 'aria-live': 'polite' }, toastMsg) : null
    );
  }

  window.ServeoneTodoListPage = ServeoneTodoListPage;
})();
