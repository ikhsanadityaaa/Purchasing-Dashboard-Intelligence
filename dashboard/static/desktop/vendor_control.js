/*
 * Serveone Dashboard Desktop: Vendor Master Page
 * Local SQLite + Excel Template/Upload/Export + Multi-Search/Multi-Filter + Internet Lookup
 * Zero Google Sheets dependency. Matches Item Registration flat card & pagination design.
 */
(function () {
  'use strict';

  const COL_WIDTHS_KEY = 'po-monitoring:vendor-control:col-widths-v1';
  const DEFAULT_COL_WIDTHS = {
    'No': 52,
    'Vendor Name': 250,
    'Alamat (Kota, Provinsi)': 215,
    'Vendor ID': 125,
    'Password': 135,
    'Brand Handling': 320,
    'Login': 92,
  };

  function loadColWidths() {
    try {
      const raw = window.localStorage.getItem(COL_WIDTHS_KEY);
      if (!raw) return Object.assign({}, DEFAULT_COL_WIDTHS);
      return Object.assign({}, DEFAULT_COL_WIDTHS, JSON.parse(raw) || {});
    } catch (_) {
      return Object.assign({}, DEFAULT_COL_WIDTHS);
    }
  }

  function saveColWidths(w) {
    try {
      window.localStorage.setItem(COL_WIDTHS_KEY, JSON.stringify(w));
    } catch (_) {}
  }

  function fmtInt(v) {
    return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(Number(v) || 0);
  }

  function svgIcon(paths, cls) {
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
          className: cls || 'w-4 h-4 flex-shrink-0',
          'aria-hidden': 'true',
        },
        ...paths.map((d, i) => h('path', { key: i, d }))
      );
    };
  }

  const ICON_SEARCH = svgIcon(['M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z', 'M21 21l-4.35-4.35']);
  const ICON_FILTER = svgIcon(['M22 3H2l8 9.46V19l4 2v-8.54L22 3z']);
  const ICON_DOWNLOAD = svgIcon(['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M7 10l5 5 5-5', 'M12 15V3']);
  const ICON_UPLOAD = svgIcon(['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8l-5-5-5 5', 'M12 3v12']);
  const ICON_GLOBE = svgIcon([
    'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z',
    'M2 12h20',
    'M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z',
  ], 'w-3.5 h-3.5 flex-shrink-0');
  const ICON_PLUS = svgIcon(['M12 5v14', 'M5 12h14']);
  const ICON_CHEVRON = svgIcon(['M6 9l6 6 6-6'], 'w-3.5 h-3.5 flex-shrink-0');
  const ICON_LEFT = svgIcon(['M15 18l-6-6 6-6']);
  const ICON_RIGHT = svgIcon(['M9 18l6-6-6-6']);
  const ICON_EYE = svgIcon(['M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z', 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z'], 'w-3.5 h-3.5');
  const ICON_BUILDING = svgIcon(['M3 21h18', 'M5 21V7l8-4v18', 'M19 21V11l-6-4'], 'w-5 h-5 text-blue-500 flex-shrink-0');

  function copyColumnValues(rows, label, getter, onDone) {
    if (!rows || !rows.length) return;
    const text = rows.map((r, i) => {
      const v = getter(r, i);
      return v == null ? '' : String(v);
    }).join('\n');
    const done = () => onDone && onDone('Copied ' + rows.length + ' values from "' + label + '" column');
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(() => {
        fallbackCopy(text);
        done();
      });
    } else {
      fallbackCopy(text);
      done();
    }
  }

  function fallbackCopy(text) {
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

  function useWindowStickyHeader(React, wrapRef, darkMode, deps) {
    React.useEffect(() => {
      const wrap = wrapRef.current;
      if (!wrap || typeof document === 'undefined') return undefined;
      let overlay = null;
      let cloneTable = null;
      let lastHtml = '';
      let rafId = null;

      const destroy = () => {
        if (overlay) {
          overlay.remove();
          overlay = null;
          cloneTable = null;
          lastHtml = '';
        }
      };

      const sync = () => {
        const table = wrap.querySelector('table');
        const thead = table ? table.querySelector('thead') : null;
        const tbody = table ? table.querySelector('tbody') : null;
        if (!table || !thead || !tbody) {
          destroy();
          return;
        }
        const wr = wrap.getBoundingClientRect();
        const hr = thead.getBoundingClientRect();
        const br = tbody.getBoundingClientRect();
        if (!(hr.top < 0 && br.bottom > hr.height + 8 && wr.width > 0)) {
          if (overlay) overlay.style.display = 'none';
          return;
        }
        if (!overlay) {
          overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed;top:0;overflow:hidden;pointer-events:none;z-index:39;box-sizing:border-box;';
          cloneTable = document.createElement('table');
          cloneTable.style.cssText = 'border-collapse:collapse;table-layout:fixed;margin:0;';
          overlay.appendChild(cloneTable);
          document.body.appendChild(overlay);
        }
        overlay.className = 'window-sticky-table-header' + (darkMode ? ' window-sticky-table-header-dark' : '');
        overlay.style.left = Math.max(0, wr.left) + 'px';
        overlay.style.width = wr.width + 'px';
        overlay.style.height = hr.height + 'px';
        overlay.style.display = 'block';

        const html = thead.outerHTML;
        if (html !== lastHtml) {
          cloneTable.innerHTML = html;
          lastHtml = html;
        }
        cloneTable.className = (table.className || '') + ' window-sticky-table-header-table';
        cloneTable.style.width = table.getBoundingClientRect().width + 'px';
        cloneTable.style.transform = 'translate3d(' + (-wrap.scrollLeft) + 'px, 0, 0)';
        const src = Array.from(thead.querySelectorAll('th'));
        const dst = Array.from(cloneTable.querySelectorAll('th'));
        for (let i = 0; i < src.length && i < dst.length; i++) {
          const w = src[i].getBoundingClientRect().width;
          dst[i].style.width = w + 'px';
          dst[i].style.minWidth = w + 'px';
          dst[i].style.maxWidth = w + 'px';
        }
      };

      const sched = () => {
        if (rafId != null) return;
        rafId = window.requestAnimationFrame(() => {
          rafId = null;
          sync();
        });
      };
      window.addEventListener('scroll', sched, { passive: true });
      window.addEventListener('resize', sched, { passive: true });
      wrap.addEventListener('scroll', sched, { passive: true });
      sched();
      return () => {
        if (rafId != null) window.cancelAnimationFrame(rafId);
        window.removeEventListener('scroll', sched);
        window.removeEventListener('resize', sched);
        wrap.removeEventListener('scroll', sched);
        destroy();
      };
    }, [React, wrapRef, darkMode, ...deps]);
  }

  function ServeoneVendorPage(props) {
    const React = props.React || window.React;
    const h = React.createElement;
    const darkMode = Boolean(props.darkMode);
    const dataRevision = props.dataRevision || '';

    const [rows, setRows] = React.useState([]);
    const [total, setTotal] = React.useState(0);
    const [page, setPage] = React.useState(1);
    const [perPage, setPerPage] = React.useState(25);
    const [totalPages, setTotalPages] = React.useState(1);
    const [stats, setStats] = React.useState({
      total_vendors: 0,
      empty_address_count: 0,
      total_brands: 0,
      so_brands_count: 0,
      web_brands_count: 0,
    });
    const [vendorOptions, setVendorOptions] = React.useState([]);
    const [brandOptions, setBrandOptions] = React.useState([]);
    const [locationOptions, setLocationOptions] = React.useState([]);

    const [searchDraft, setSearchDraft] = React.useState('');
    const [searchApplied, setSearchApplied] = React.useState('');
    const [selectedVendors, setSelectedVendors] = React.useState([]);
    const [selectedBrands, setSelectedBrands] = React.useState([]);
    const [selectedLocations, setSelectedLocations] = React.useState([]);

    const [openPop, setOpenPop] = React.useState(null); // 'search' | 'vendor' | 'brand' | 'location' | null
    const [popFilterText, setPopFilterText] = React.useState('');
    const [filtersOpen, setFiltersOpen] = React.useState(false);
    const [addOpen, setAddOpen] = React.useState(false);
    const [newVendor, setNewVendor] = React.useState({ vendor_name: '', city_province: '', vendor_id: '', password: '', web_brands: '' });

    const [editing, setEditing] = React.useState(null); // { id, field, value }
    const [detailModal, setDetailModal] = React.useState(null); // { id, row_key, vendor_name, city_province, vendor_id, password, web_brands_str, so_brands, source_url, show_pwd }
    const [savingModal, setSavingModal] = React.useState(false);
    const [showPwdMap, setShowPwdMap] = React.useState({});
    const [lookupBusyMap, setLookupBusyMap] = React.useState({});
    const [batchLookupBusy, setBatchLookupBusy] = React.useState(false);
    const [uploading, setUploading] = React.useState(false);
    const [loading, setLoading] = React.useState(true);
    const [error, setError] = React.useState('');
    const [toastMsg, setToastMsg] = React.useState('');

    const [colWidths, setColWidths] = React.useState(() => loadColWidths());
    const resizingRef = React.useRef(false);
    const tableWrapRef = React.useRef(null);
    const fileInputRef = React.useRef(null);
    const toastTimerRef = React.useRef(null);

    const showToast = React.useCallback((msg) => {
      setToastMsg(msg);
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
      toastTimerRef.current = window.setTimeout(() => setToastMsg(''), 3000);
    }, []);

    useWindowStickyHeader(React, tableWrapRef, darkMode, [rows, colWidths, page, perPage]);

    React.useEffect(() => {
      if (!openPop) return;
      const onKey = (e) => { if (e.key === 'Escape') setOpenPop(null); };
      window.addEventListener('keydown', onKey);
      return () => window.removeEventListener('keydown', onKey);
    }, [openPop]);

    const buildQueryParams = React.useCallback((includePaging) => {
      const qs = new URLSearchParams();
      if (includePaging) {
        qs.set('page', String(page));
        qs.set('per_page', String(perPage));
      }
      if (searchApplied) qs.set('search', searchApplied);
      selectedVendors.forEach((v) => qs.append('vendor', v));
      selectedBrands.forEach((b) => qs.append('brand', b));
      selectedLocations.forEach((l) => qs.append('location', l));
      return qs;
    }, [page, perPage, searchApplied, selectedVendors, selectedBrands, selectedLocations]);

    const fetchVendors = React.useCallback(async () => {
      setLoading(true);
      setError('');
      try {
        const qs = buildQueryParams(true);
        const res = await fetch('/api/vendor-control/data?' + qs.toString(), {
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Gagal memuat data Vendor (HTTP ' + res.status + ')');
        setRows(Array.isArray(json.data) ? json.data : []);
        setTotal(Number(json.total || 0));
        setTotalPages(Math.max(1, Number(json.total_pages || 1)));
        if (json.stats) setStats(json.stats);
        if (Array.isArray(json.vendor_options)) setVendorOptions(json.vendor_options);
        if (Array.isArray(json.brand_options)) setBrandOptions(json.brand_options);
        if (Array.isArray(json.location_options)) setLocationOptions(json.location_options);
      } catch (err) {
        setError(err && err.message ? err.message : 'Gagal memuat data Vendor');
      } finally {
        setLoading(false);
      }
    }, [buildQueryParams]);

    React.useEffect(() => {
      fetchVendors();
    }, [fetchVendors, dataRevision]);

    const getHeaders = () => {
      const hdrs = { 'Content-Type': 'application/json', Accept: 'application/json' };
      if (window.__serveoneDesktopUploadToken) hdrs['X-Serveone-Desktop-Upload'] = window.__serveoneDesktopUploadToken;
      return hdrs;
    };

    const saveInlineEdit = async (rowId, field, value) => {
      setEditing(null);
      try {
        const res = await fetch('/api/vendor-control/' + encodeURIComponent(rowId), {
          method: 'PUT',
          credentials: 'same-origin',
          headers: getHeaders(),
          body: JSON.stringify({ field, value }),
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Gagal menyimpan');
        showToast('Berhasil disimpan');
        fetchVendors();
      } catch (err) {
        showToast('Error: ' + (err.message || 'Gagal menyimpan'));
      }
    };

    const openVendorModal = (row) => {
      const bh = row.brand_handling || { cheapest: [], all: [], web: [] };
      setDetailModal({
        id: row.id,
        row_key: row.row_key,
        vendor_name: row.vendor_name || '',
        city_province: row.city_province || '',
        vendor_id: row.vendor_id || '',
        password: row.password || '',
        web_brands_str: (row.web_brands || bh.web || []).join(', '),
        so_brands: bh.all || [],
        so_cheapest: bh.cheapest || [],
        source_url: row.source_url || '',
        show_pwd: false,
      });
    };

    const saveVendorModal = async (e) => {
      e.preventDefault();
      if (!detailModal) return;
      if (!detailModal.vendor_name.trim()) {
        showToast('Vendor Name tidak boleh kosong');
        return;
      }
      setSavingModal(true);
      try {
        const res = await fetch('/api/vendor-control/' + encodeURIComponent(detailModal.id), {
          method: 'PUT',
          credentials: 'same-origin',
          headers: getHeaders(),
          body: JSON.stringify({
            fields: {
              vendor_name: detailModal.vendor_name,
              city_province: detailModal.city_province,
              vendor_id: detailModal.vendor_id,
              password: detailModal.password,
              web_brands: detailModal.web_brands_str,
            },
          }),
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Gagal menyimpan detail vendor');
        showToast('Detail vendor "' + detailModal.vendor_name + '" berhasil disimpan');
        setDetailModal(null);
        fetchVendors();
      } catch (err) {
        showToast('Error: ' + (err.message || 'Gagal menyimpan'));
      } finally {
        setSavingModal(false);
      }
    };

    const handleInternetLookup = async () => {
      setBatchLookupBusy(true);
      try {
        const res = await fetch('/api/vendor-control/lookup-internet', {
          method: 'POST',
          credentials: 'same-origin',
          headers: getHeaders(),
          body: JSON.stringify({ mode: 'both', overwrite_address: false }),
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Pencarian internet gagal');
        if (json.message) {
          showToast(json.message);
        } else {
          const results = json.results || [];
          const foundCount = results.filter((r) => r.found).length;
          showToast('Pencarian internet selesai: ' + foundCount + ' dari ' + results.length + ' vendor kosong ditemukan');
        }
        fetchVendors();
      } catch (err) {
        showToast('Pencarian internet gagal: ' + (err.message || 'error'));
      } finally {
        setBatchLookupBusy(false);
      }
    };

    const handleExcelUpload = async (e) => {
      const file = e.target.files && e.target.files[0];
      e.target.value = '';
      if (!file) return;
      setUploading(true);
      try {
        const fd = new FormData();
        fd.append('file', file);
        const hdrs = {};
        if (window.__serveoneDesktopUploadToken) hdrs['X-Serveone-Desktop-Upload'] = window.__serveoneDesktopUploadToken;
        const res = await fetch('/api/vendor-control/upload', {
          method: 'POST',
          credentials: 'same-origin',
          headers: hdrs,
          body: fd,
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Upload Excel gagal');
        showToast(json.message || 'Upload Excel berhasil');
        setPage(1);
        fetchVendors();
      } catch (err) {
        showToast('Error: ' + (err.message || 'Upload Excel gagal'));
      } finally {
        setUploading(false);
      }
    };

    const handleAddVendor = async (e) => {
      e.preventDefault();
      if (!newVendor.vendor_name.trim()) {
        showToast('Vendor Name wajib diisi');
        return;
      }
      try {
        const res = await fetch('/api/vendor-control/add', {
          method: 'POST',
          credentials: 'same-origin',
          headers: getHeaders(),
          body: JSON.stringify(newVendor),
        });
        const json = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(json.error || 'Gagal menambah vendor');
        showToast(json.message || 'Vendor ditambahkan');
        setNewVendor({ vendor_name: '', city_province: '', vendor_id: '', password: '', web_brands: '' });
        setAddOpen(false);
        setPage(1);
        fetchVendors();
      } catch (err) {
        showToast('Error: ' + (err.message || 'Gagal menambah vendor'));
      }
    };

    const startColResize = (e, colKey, currentW) => {
      e.preventDefault();
      e.stopPropagation();
      const startX = e.clientX;
      const startW = Number(currentW) || 140;
      resizingRef.current = true;
      const onMove = (ev) => {
        const nextW = Math.min(720, Math.max(48, Math.round(startW + (ev.clientX - startX))));
        setColWidths((prev) => Object.assign({}, prev, { [colKey]: nextW }));
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        setColWidths((prev) => {
          saveColWidths(prev);
          return prev;
        });
        window.setTimeout(() => { resizingRef.current = false; }, 50);
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    };

    const renderMultiSelectDropdown = (popKey, label, options, selected, setSelected) => {
      const isOpen = openPop === popKey;
      const filteredOpts = popFilterText.trim()
        ? options.filter((o) => o.toLowerCase().includes(popFilterText.trim().toLowerCase()))
        : options;
      const shownOpts = filteredOpts.slice(0, 120);
      const toggleOpt = (opt) => {
        setPage(1);
        setSelected((prev) => (prev.includes(opt) ? prev.filter((x) => x !== opt) : [...prev, opt]));
      };
      return h(
        'div',
        { className: 'vc-dropdown-anchor' },
        h(
          'button',
          {
            type: 'button',
            className: 'vc-btn' + (isOpen || selected.length > 0 ? ' is-active' : ''),
            onClick: () => {
              setPopFilterText('');
              setOpenPop(isOpen ? null : popKey);
            },
          },
          h('span', null, label),
          selected.length > 0 ? h('span', { className: 'vc-pill' }, selected.length) : null,
          ICON_CHEVRON(h)
        ),
        isOpen
          ? h(
              React.Fragment,
              null,
              h('div', { className: 'vc-backdrop', onClick: () => setOpenPop(null) }),
              h(
                'div',
                { className: 'vc-popover is-left', role: 'dialog', 'aria-label': 'Filter ' + label },
                h(
                  'div',
                  { style: { padding: '0.625rem', borderBottom: '1px solid #E5E7EB', display: 'flex', flexDirection: 'column', gap: '0.375rem' } },
                  h('input', {
                    type: 'search',
                    className: 'vc-input',
                    style: { width: '100%', height: '32px' },
                    placeholder: 'Cari ' + label + '...',
                    value: popFilterText,
                    autoFocus: true,
                    onChange: (e) => setPopFilterText(e.target.value),
                  }),
                  h(
                    'div',
                    { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.6875rem' } },
                    h('span', null, selected.length + ' dipilih (bisa lebih dari 1)'),
                    selected.length > 0
                      ? h(
                          'button',
                          {
                            type: 'button',
                            className: 'vc-mini-btn',
                            onClick: () => { setPage(1); setSelected([]); },
                          },
                          'Clear'
                        )
                      : null
                  )
                ),
                h(
                  'div',
                  { className: 'vc-popover-list' },
                  shownOpts.length === 0
                    ? h('div', { style: { padding: '0.75rem', fontSize: '0.75rem', color: '#6B7280', textAlign: 'center' } }, 'Tidak ada opsi cocok')
                    : shownOpts.map((opt) =>
                        h(
                          'label',
                          { key: opt, className: 'vc-popover-item vc-clickable' },
                          h('input', {
                            type: 'checkbox',
                            checked: selected.includes(opt),
                            onChange: () => toggleOpt(opt),
                          }),
                          h('span', { style: { wordBreak: 'break-word' } }, opt)
                        )
                      )
                )
              )
            )
          : null
      );
    };

    const activeFilterCount =
      (selectedVendors.length > 0 ? 1 : 0) +
      (selectedBrands.length > 0 ? 1 : 0) +
      (selectedLocations.length > 0 ? 1 : 0);

    const fromRow = total === 0 ? 0 : (page - 1) * perPage + 1;
    const toRow = Math.min(total, page * perPage);

    const missingAddressIdsOnPage = rows.filter((r) => !r.city_province).map((r) => r.id).slice(0, 10);

    const columns = [
      ['No', (r, i) => (page - 1) * perPage + i + 1],
      ['Vendor Name', (r) => r.vendor_name],
      ['Alamat (Kota, Provinsi)', (r) => r.city_province],
      ['Vendor ID', (r) => r.vendor_id],
      ['Password', (r) => r.password],
      ['Brand Handling', (r) => {
        const bh = r.brand_handling || {};
        return [...(bh.all || []), ...(bh.web || [])].join(', ');
      }],
      ['Login', (r) => (r.vendor_id ? 'Login' : '')],
    ];

    return h(
      'div',
      { className: 'vc-page' + (darkMode ? ' is-dark' : '') },

      // Main Vendor Card
      h(
        'section',
        { className: 'vc-card', 'aria-label': 'Daftar Vendor' },
        h(
          'div',
          { className: 'vc-card-header' },
          h(
            'div',
            { className: 'vc-title-group' },
            ICON_BUILDING(h),
            h('h2', { className: 'vc-title' }, 'Vendor'),
            h('span', { className: 'vc-count' }, fmtInt(total) + ' records')
          ),
          h(
            'div',
            { className: 'vc-actions' },

            // Universal Multi-Term Search Popover (replaces Refresh Sheet & More)
            h(
              'div',
              { className: 'vc-dropdown-anchor' },
              h(
                'button',
                {
                  type: 'button',
                  id: 'vc-search-btn',
                  className: 'vc-btn' + (openPop === 'search' || searchApplied ? ' is-active' : ''),
                  onClick: () => setOpenPop(openPop === 'search' ? null : 'search'),
                },
                ICON_SEARCH(h),
                h('span', null, searchApplied ? 'Search: ' + searchApplied.slice(0, 16) : 'Search'),
                ICON_CHEVRON(h)
              ),
              openPop === 'search'
                ? h(
                    React.Fragment,
                    null,
                    h('div', { className: 'vc-backdrop', onClick: () => setOpenPop(null) }),
                    h(
                      'div',
                      { className: 'vc-popover', role: 'dialog', 'aria-label': 'Search Vendor, Alamat, Brand' },
                      h(
                        'form',
                        {
                          onSubmit: (e) => {
                            e.preventDefault();
                            setPage(1);
                            setSearchApplied(searchDraft.trim());
                            setOpenPop(null);
                          },
                          style: { padding: '0.75rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' },
                        },
                        h('label', { htmlFor: 'vc-search-input', className: 'vc-field-label' }, 'Cari Apapun (Vendor, Alamat, Brand, ID)'),
                        h('textarea', {
                          id: 'vc-search-input',
                          rows: 3,
                          className: 'vc-textarea',
                          placeholder: 'Bisa lebih dari 1 (pisahkan koma atau baris baru):\nOmron, SMC\nBekasi\nPT Contoh',
                          value: searchDraft,
                          autoFocus: true,
                          onChange: (e) => setSearchDraft(e.target.value),
                        }),
                        h(
                          'div',
                          { style: { display: 'flex', justifyContent: 'flex-end', gap: '0.5rem' } },
                          h(
                            'button',
                            {
                              type: 'button',
                              className: 'vc-mini-btn',
                              onClick: () => {
                                setSearchDraft('');
                                setSearchApplied('');
                                setPage(1);
                                setOpenPop(null);
                              },
                            },
                            'Clear'
                          ),
                          h('button', { type: 'submit', className: 'vc-btn is-primary', style: { padding: '0.3125rem 0.75rem' } }, 'Search')
                        )
                      )
                    )
                  )
                : null
            ),

            // Filters button (Multi-select Vendor, Brand, Alamat)
            h(
              'button',
              {
                type: 'button',
                id: 'vc-filters-btn',
                className: 'vc-btn' + (filtersOpen || activeFilterCount > 0 ? ' is-active' : ''),
                onClick: () => setFiltersOpen((v) => !v),
              },
              ICON_FILTER(h),
              h('span', null, 'Filters'),
              activeFilterCount > 0 ? h('span', { className: 'vc-pill' }, activeFilterCount) : null,
              ICON_CHEVRON(h)
            ),

            // Single Cari Internet button (auto-searches empty addresses/brands only, never overwrites edited)
            h(
              'button',
              {
                type: 'button',
                id: 'vc-cari-internet-btn',
                className: 'vc-btn',
                disabled: batchLookupBusy || Number(stats.empty_address_count || 0) === 0,
                onClick: handleInternetLookup,
                title: 'Cari otomatis alamat (Kota, Provinsi) & brand dari internet hanya untuk vendor yang masih kosong',
              },
              ICON_GLOBE(h),
              h('span', null, batchLookupBusy ? 'Mencari Internet...' : 'Cari Internet')
            ),

            // Add Vendor button
            h(
              'button',
              {
                type: 'button',
                className: 'vc-btn' + (addOpen ? ' is-active' : ''),
                onClick: () => setAddOpen((v) => !v),
              },
              ICON_PLUS(h),
              h('span', null, 'Tambah')
            ),

            // Template Excel
            h(
              'a',
              {
                href: '/api/vendor-control/template',
                className: 'vc-btn',
                style: { textDecoration: 'none' },
                title: 'Download Template Excel Vendor',
              },
              ICON_DOWNLOAD(h),
              h('span', null, 'Template Excel')
            ),

            // Upload Excel
            h(
              'button',
              {
                type: 'button',
                className: 'vc-btn',
                disabled: uploading,
                onClick: () => fileInputRef.current && fileInputRef.current.click(),
              },
              ICON_UPLOAD(h),
              h('span', null, uploading ? 'Uploading...' : 'Upload Excel')
            ),
            h('input', {
              ref: fileInputRef,
              type: 'file',
              accept: '.xlsx,.xls',
              style: { display: 'none' },
              onChange: handleExcelUpload,
            }),

            // Download Excel
            h(
              'a',
              {
                href: '/api/vendor-control/export?' + buildQueryParams(false).toString(),
                className: 'vc-btn is-primary',
                style: { textDecoration: 'none' },
              },
              ICON_DOWNLOAD(h),
              h('span', null, 'Download Excel')
            )
          )
        ),

        // Collapsible Multi-Select Filters Drawer
        filtersOpen
          ? h(
              'div',
              { className: 'vc-drawer', role: 'region', 'aria-label': 'Filter Multi-Pilih Vendor & Brand' },
              renderMultiSelectDropdown('vendor', 'Vendor Name', vendorOptions, selectedVendors, setSelectedVendors),
              renderMultiSelectDropdown('brand', 'Brand Handling', brandOptions, selectedBrands, setSelectedBrands),
              renderMultiSelectDropdown('location', 'Kota / Provinsi', locationOptions, selectedLocations, setSelectedLocations),
              activeFilterCount > 0 || searchApplied
                ? h(
                    'button',
                    {
                      type: 'button',
                      className: 'vc-mini-btn',
                      style: { height: '36px', padding: '0 0.75rem' },
                      onClick: () => {
                        setSelectedVendors([]);
                        setSelectedBrands([]);
                        setSelectedLocations([]);
                        setSearchDraft('');
                        setSearchApplied('');
                        setPage(1);
                      },
                    },
                    'Reset Semua Filter'
                  )
                : null
            )
          : null,

        // Inline Add Vendor Form Drawer
        addOpen
          ? h(
              'form',
              { className: 'vc-drawer', onSubmit: handleAddVendor },
              h(
                'div',
                { className: 'vc-field' },
                h('label', { className: 'vc-field-label' }, 'Vendor Name *'),
                h('input', {
                  className: 'vc-input',
                  placeholder: 'PT Contoh Supplier',
                  value: newVendor.vendor_name,
                  onChange: (e) => setNewVendor(Object.assign({}, newVendor, { vendor_name: e.target.value })),
                })
              ),
              h(
                'div',
                { className: 'vc-field' },
                h('label', { className: 'vc-field-label' }, 'Alamat (Kota, Provinsi)'),
                h('input', {
                  className: 'vc-input',
                  placeholder: 'Bekasi, Jawa Barat',
                  value: newVendor.city_province,
                  onChange: (e) => setNewVendor(Object.assign({}, newVendor, { city_province: e.target.value })),
                })
              ),
              h(
                'div',
                { className: 'vc-field', style: { minWidth: '130px' } },
                h('label', { className: 'vc-field-label' }, 'Vendor ID'),
                h('input', {
                  className: 'vc-input',
                  placeholder: 'Opsional',
                  value: newVendor.vendor_id,
                  onChange: (e) => setNewVendor(Object.assign({}, newVendor, { vendor_id: e.target.value })),
                })
              ),
              h(
                'div',
                { className: 'vc-field', style: { minWidth: '130px' } },
                h('label', { className: 'vc-field-label' }, 'Password'),
                h('input', {
                  className: 'vc-input',
                  placeholder: 'Opsional',
                  value: newVendor.password,
                  onChange: (e) => setNewVendor(Object.assign({}, newVendor, { password: e.target.value })),
                })
              ),
              h(
                'div',
                { className: 'vc-field' },
                h('label', { className: 'vc-field-label' }, 'Brand (pisah koma)'),
                h('input', {
                  className: 'vc-input',
                  placeholder: 'Omron, SMC',
                  value: newVendor.web_brands,
                  onChange: (e) => setNewVendor(Object.assign({}, newVendor, { web_brands: e.target.value })),
                })
              ),
              h('button', { type: 'submit', className: 'vc-btn is-primary', style: { height: '36px' } }, 'Simpan Vendor')
            )
          : null,

        // Active Filter Chips
        (searchApplied || selectedVendors.length > 0 || selectedBrands.length > 0 || selectedLocations.length > 0)
          ? h(
              'div',
              { className: 'vc-chips-bar' },
              searchApplied
                ? h(
                    'span',
                    { className: 'vc-chip' },
                    'Search: ' + searchApplied,
                    h('button', { type: 'button', style: { border: 'none', background: 'transparent', fontWeight: 800 }, onClick: () => { setSearchDraft(''); setSearchApplied(''); setPage(1); } }, '×')
                  )
                : null,
              selectedVendors.map((v) =>
                h(
                  'span',
                  { key: 'v-' + v, className: 'vc-chip' },
                  'Vendor: ' + v,
                  h('button', { type: 'button', style: { border: 'none', background: 'transparent', fontWeight: 800 }, onClick: () => { setPage(1); setSelectedVendors((p) => p.filter((x) => x !== v)); } }, '×')
                )
              ),
              selectedBrands.map((b) =>
                h(
                  'span',
                  { key: 'b-' + b, className: 'vc-chip' },
                  'Brand: ' + b,
                  h('button', { type: 'button', style: { border: 'none', background: 'transparent', fontWeight: 800 }, onClick: () => { setPage(1); setSelectedBrands((p) => p.filter((x) => x !== b)); } }, '×')
                )
              ),
              selectedLocations.map((l) =>
                h(
                  'span',
                  { key: 'l-' + l, className: 'vc-chip' },
                  'Alamat: ' + l,
                  h('button', { type: 'button', style: { border: 'none', background: 'transparent', fontWeight: 800 }, onClick: () => { setPage(1); setSelectedLocations((p) => p.filter((x) => x !== l)); } }, '×')
                )
              )
            )
          : null,

        error
          ? h('div', { style: { padding: '1rem 1.25rem', color: '#DC2626', fontWeight: 700 } }, error)
          : null,

        // Data Table
        h(
          'div',
          { ref: tableWrapRef, className: 'vc-table-wrap' },
          h(
            'table',
            { className: 'vc-table' },
            h(
              'colgroup',
              null,
              columns.map(([label]) =>
                h('col', { key: label, style: { width: (colWidths[label] || DEFAULT_COL_WIDTHS[label] || 140) + 'px' } })
              )
            ),
            h(
              'thead',
              null,
              h(
                'tr',
                null,
                columns.map(([label, getter]) => {
                  const w = colWidths[label] || DEFAULT_COL_WIDTHS[label] || 140;
                  return h(
                    'th',
                    {
                      key: label,
                      style: { width: w + 'px' },
                      title: label + ' (Ctrl+click: copy kolom · drag tepi kanan: ubah lebar)',
                      onClick: (e) => {
                        if (resizingRef.current) return;
                        if (e.ctrlKey || e.metaKey || e.shiftKey) {
                          e.preventDefault();
                          copyColumnValues(rows, label, getter, showToast);
                        }
                      },
                    },
                    label,
                    h('div', {
                      className: 'vc-col-resizer',
                      title: 'Drag untuk ubah lebar kolom',
                      onMouseDown: (e) => startColResize(e, label, w),
                      onClick: (e) => e.stopPropagation(),
                    })
                  );
                })
              )
            ),
            h(
              'tbody',
              null,
              loading && rows.length === 0
                ? h('tr', null, h('td', { colSpan: 7, style: { padding: '2rem', textAlign: 'center', color: '#6B7280' } }, 'Memuat data Vendor...'))
                : rows.length === 0
                ? h('tr', null, h('td', { colSpan: 7, style: { padding: '2rem', textAlign: 'center', color: '#6B7280' } }, 'Tidak ada data Vendor yang cocok.'))
                : rows.map((row, idx) => {
                    const rowNo = (page - 1) * perPage + idx + 1;
                    const bh = row.brand_handling || { cheapest: [], all: [], web: [] };
                    const cheapestSet = new Set((bh.cheapest || []).map((x) => x.toLowerCase()));
                    const soBrands = bh.all || [];
                    const webBrands = bh.web || [];
                    const isBusy = Boolean(lookupBusyMap[row.id]);
                    const showPwd = Boolean(showPwdMap[row.id]);

                    const renderInlineCell = (field, displayVal, placeholder, isPwd) => {
                      const isEd = editing && editing.id === row.id && editing.field === field;
                      if (isEd) {
                        return h('input', {
                          type: isPwd && !showPwd ? 'password' : 'text',
                          className: 'vc-input',
                          style: { width: '100%', height: '30px' },
                          placeholder,
                          value: editing.value,
                          autoFocus: true,
                          onChange: (e) => setEditing(Object.assign({}, editing, { value: e.target.value })),
                          onBlur: () => saveInlineEdit(row.id, field, editing.value),
                          onKeyDown: (e) => {
                            if (e.key === 'Enter') saveInlineEdit(row.id, field, editing.value);
                            if (e.key === 'Escape') setEditing(null);
                          },
                        });
                      }
                      const shown = isPwd && displayVal && !showPwd ? '••••••••' : displayVal;
                      return h(
                        'button',
                        {
                          type: 'button',
                          className: 'vc-inline-btn',
                          title: 'Klik untuk edit ' + field,
                          onClick: () => setEditing({ id: row.id, field, value: displayVal || '' }),
                        },
                        h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: shown ? 'inherit' : '#9CA3AF' } }, shown || placeholder)
                      );
                    };

                    return h(
                      'tr',
                      { key: row.id },
                      h('td', { style: { textAlign: 'center', fontWeight: 700 } }, rowNo),
                      h(
                        'td',
                        null,
                        h(
                          'button',
                          {
                            type: 'button',
                            className: 'vc-vendor-link',
                            title: 'Klik untuk lihat & edit detail lengkap vendor',
                            onClick: () => openVendorModal(row),
                          },
                          row.vendor_name
                        )
                      ),
                      h(
                        'td',
                        null,
                        renderInlineCell('city_province', row.city_province, 'Isi Kota, Provinsi...', false)
                      ),
                      h('td', { style: { fontFamily: 'monospace' } }, renderInlineCell('vendor_id', row.vendor_id, '-', false)),
                      h(
                        'td',
                        { style: { fontFamily: 'monospace' } },
                        h(
                          'div',
                          { style: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.25rem' } },
                          h('div', { style: { flex: '1 1 auto', minWidth: 0 } }, renderInlineCell('password', row.password, '-', true)),
                          row.password
                            ? h(
                                'button',
                                {
                                  type: 'button',
                                  className: 'vc-mini-btn',
                                  title: showPwd ? 'Sembunyikan password' : 'Tampilkan password',
                                  onClick: () => setShowPwdMap((m) => Object.assign({}, m, { [row.id]: !m[row.id] })),
                                },
                                ICON_EYE(h)
                              )
                            : null
                        )
                      ),
                      h(
                        'td',
                        null,
                        editing && editing.id === row.id && editing.field === 'web_brands'
                          ? h('input', {
                              type: 'text',
                              className: 'vc-input',
                              style: { width: '100%', height: '30px' },
                              placeholder: 'Omron, SMC, Schneider (pisah koma)',
                              value: editing.value,
                              autoFocus: true,
                              onChange: (e) => setEditing(Object.assign({}, editing, { value: e.target.value })),
                              onBlur: () => saveInlineEdit(row.id, 'web_brands', editing.value),
                              onKeyDown: (e) => {
                                if (e.key === 'Enter') saveInlineEdit(row.id, 'web_brands', editing.value);
                                if (e.key === 'Escape') setEditing(null);
                              },
                            })
                          : h(
                              'div',
                              { className: 'vc-brand-wrap' },
                              soBrands.slice(0, 8).map((b) =>
                                h(
                                  'span',
                                  {
                                    key: 'so-' + b,
                                    className: 'vc-brand-tag' + (cheapestSet.has(b.toLowerCase()) ? ' is-cheapest' : ''),
                                    title: cheapestSet.has(b.toLowerCase()) ? b + ' (Termurah di SO Data)' : b + ' (SO Data)',
                                  },
                                  b
                                )
                              ),
                              soBrands.length > 8
                                ? h('span', { className: 'vc-brand-tag', title: soBrands.join(', ') }, '+' + (soBrands.length - 8))
                                : null,
                              webBrands.map((b) =>
                                h(
                                  'span',
                                  { key: 'web-' + b, className: 'vc-brand-tag is-web', title: b + ' (Info Internet)' },
                                  b + ' · Web'
                                )
                              ),
                              h(
                                'button',
                                {
                                  type: 'button',
                                  className: 'vc-mini-btn',
                                  title: 'Edit / tambah brand internet secara manual',
                                  onClick: () => setEditing({ id: row.id, field: 'web_brands', value: (row.web_brands || []).join(', ') }),
                                },
                                '+ Brand'
                              )
                            )
                      ),
                      h(
                        'td',
                        { style: { textAlign: 'center' } },
                        row.vendor_id && row.password
                          ? h(
                              'a',
                              {
                                href: '/api/vendor-control/login/' + encodeURIComponent(row.row_key),
                                target: '_blank',
                                rel: 'noopener noreferrer',
                                className: 'vc-mini-btn',
                                style: { textDecoration: 'none' },
                              },
                              'Login'
                            )
                          : h('span', { style: { color: '#9CA3AF' } }, '-')
                      )
                    );
                  })
            )
          )
        ),

        // Pagination (exact Item Registration design)
        h(
          'div',
          { className: 'vc-pager' },
          h(
            'div',
            { style: { display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' } },
            h('span', null, 'Showing ' + fmtInt(fromRow) + '-' + fmtInt(toRow) + ' of ' + fmtInt(total)),
            h(
              'label',
              { htmlFor: 'vc-rows-sel', style: { display: 'inline-flex', alignItems: 'center', gap: '0.25rem', fontSize: '0.75rem' } },
              'Rows',
              h(
                'select',
                {
                  id: 'vc-rows-sel',
                  className: 'vc-select',
                  style: { height: '28px', padding: '0.125rem 0.5rem' },
                  value: perPage,
                  onChange: (e) => {
                    setPage(1);
                    setPerPage(Number(e.target.value) || 25);
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
                className: 'vc-pager-btn',
                disabled: page <= 1,
                onClick: () => setPage(Math.max(1, page - 1)),
                'aria-label': 'Halaman sebelumnya',
              },
              ICON_LEFT(h)
            ),
            h('span', { className: 'vc-pager-pill' }, page + '/' + totalPages),
            h(
              'button',
              {
                type: 'button',
                className: 'vc-pager-btn',
                disabled: page >= totalPages,
                onClick: () => setPage(Math.min(totalPages, page + 1)),
                'aria-label': 'Halaman berikutnya',
              },
              ICON_RIGHT(h)
            )
          )
        )
      ),

      // Vendor Detail & Edit Modal
      detailModal
        ? h(
            'div',
            {
              className: 'vc-modal-overlay',
              onClick: (e) => {
                if (e.target === e.currentTarget && !savingModal) setDetailModal(null);
              },
            },
            h(
              'form',
              {
                className: 'vc-modal',
                role: 'dialog',
                'aria-modal': 'true',
                'aria-label': 'Detail Vendor',
                onSubmit: saveVendorModal,
              },
              h(
                'div',
                { className: 'vc-modal-head' },
                h(
                  'div',
                  { style: { display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 } },
                  ICON_BUILDING(h),
                  h('h3', { style: { margin: 0, fontSize: '1rem', fontWeight: 700 } }, 'Detail Vendor')
                ),
                h(
                  'button',
                  {
                    type: 'button',
                    className: 'vc-mini-btn',
                    onClick: () => setDetailModal(null),
                    'aria-label': 'Tutup',
                  },
                  '✕'
                )
              ),
              h(
                'div',
                { className: 'vc-modal-body' },
                h(
                  'div',
                  { className: 'vc-field' },
                  h('label', { htmlFor: 'vc-m-name', className: 'vc-field-label' }, 'Vendor Name *'),
                  h('input', {
                    id: 'vc-m-name',
                    type: 'text',
                    className: 'vc-input',
                    value: detailModal.vendor_name,
                    onChange: (e) => setDetailModal(Object.assign({}, detailModal, { vendor_name: e.target.value })),
                  })
                ),
                h(
                  'div',
                  { className: 'vc-field' },
                  h('label', { htmlFor: 'vc-m-addr', className: 'vc-field-label' }, 'Alamat Perusahaan (Kota, Provinsi)'),
                  h('input', {
                    id: 'vc-m-addr',
                    type: 'text',
                    className: 'vc-input',
                    placeholder: 'Contoh: Bekasi, Jawa Barat',
                    value: detailModal.city_province,
                    onChange: (e) => setDetailModal(Object.assign({}, detailModal, { city_province: e.target.value })),
                  })
                ),
                h(
                  'div',
                  { className: 'vc-modal-grid' },
                  h(
                    'div',
                    { className: 'vc-field' },
                    h('label', { htmlFor: 'vc-m-vid', className: 'vc-field-label' }, 'Vendor ID'),
                    h('input', {
                      id: 'vc-m-vid',
                      type: 'text',
                      className: 'vc-input',
                      value: detailModal.vendor_id,
                      onChange: (e) => setDetailModal(Object.assign({}, detailModal, { vendor_id: e.target.value })),
                    })
                  ),
                  h(
                    'div',
                    { className: 'vc-field' },
                    h('label', { htmlFor: 'vc-m-pwd', className: 'vc-field-label' }, 'Password'),
                    h(
                      'div',
                      { style: { display: 'flex', gap: '0.375rem', alignItems: 'center' } },
                      h('input', {
                        id: 'vc-m-pwd',
                        type: detailModal.show_pwd ? 'text' : 'password',
                        className: 'vc-input',
                        style: { flex: '1 1 auto' },
                        value: detailModal.password,
                        onChange: (e) => setDetailModal(Object.assign({}, detailModal, { password: e.target.value })),
                      }),
                      h(
                        'button',
                        {
                          type: 'button',
                          className: 'vc-mini-btn',
                          style: { height: '36px', padding: '0 0.5rem' },
                          onClick: () => setDetailModal(Object.assign({}, detailModal, { show_pwd: !detailModal.show_pwd })),
                          title: detailModal.show_pwd ? 'Sembunyikan password' : 'Tampilkan password',
                        },
                        ICON_EYE(h)
                      )
                    )
                  )
                ),
                h(
                  'div',
                  { className: 'vc-field' },
                  h('label', { htmlFor: 'vc-m-brands', className: 'vc-field-label' }, 'Brand Handling (Internet / Manual, pisah koma)'),
                  h('input', {
                    id: 'vc-m-brands',
                    type: 'text',
                    className: 'vc-input',
                    placeholder: 'Omron, SMC, Schneider',
                    value: detailModal.web_brands_str,
                    onChange: (e) => setDetailModal(Object.assign({}, detailModal, { web_brands_str: e.target.value })),
                  })
                ),
                detailModal.so_brands && detailModal.so_brands.length > 0
                  ? h(
                      'div',
                      { className: 'vc-field' },
                      h('span', { className: 'vc-field-label' }, 'Brand Handling dari SO Data (' + detailModal.so_brands.length + ')'),
                      h(
                        'div',
                        { className: 'vc-brand-wrap' },
                        detailModal.so_brands.map((b) =>
                          h(
                            'span',
                            {
                              key: 'm-so-' + b,
                              className: 'vc-brand-tag' + (detailModal.so_cheapest.includes(b) ? ' is-cheapest' : ''),
                            },
                            b
                          )
                        )
                      )
                    )
                  : null,
                detailModal.source_url
                  ? h(
                      'div',
                      { style: { fontSize: '0.75rem', color: '#6B7280', wordBreak: 'break-all' } },
                      'Sumber internet: ',
                      h('a', { href: detailModal.source_url, target: '_blank', rel: 'noopener noreferrer', style: { color: '#2563EB' } }, detailModal.source_url)
                    )
                  : null
              ),
              h(
                'div',
                { className: 'vc-modal-foot' },
                detailModal.vendor_id && detailModal.password
                  ? h(
                      'a',
                      {
                        href: '/api/vendor-control/login/' + encodeURIComponent(detailModal.row_key),
                        target: '_blank',
                        rel: 'noopener noreferrer',
                        className: 'vc-btn',
                        style: { textDecoration: 'none' },
                      },
                      'Login Vendor'
                    )
                  : h('span', null),
                h(
                  'div',
                  { style: { display: 'flex', gap: '0.5rem' } },
                  h(
                    'button',
                    {
                      type: 'button',
                      className: 'vc-btn',
                      disabled: savingModal,
                      onClick: () => setDetailModal(null),
                    },
                    'Batal'
                  ),
                  h(
                    'button',
                    {
                      type: 'submit',
                      className: 'vc-btn is-primary',
                      disabled: savingModal,
                    },
                    savingModal ? 'Menyimpan...' : 'Simpan'
                  )
                )
              )
            )
          )
        : null,

      toastMsg ? h('div', { className: 'vc-toast', role: 'status', 'aria-live': 'polite' }, toastMsg) : null
    );
  }

  window.ServeoneVendorPage = ServeoneVendorPage;
})();
