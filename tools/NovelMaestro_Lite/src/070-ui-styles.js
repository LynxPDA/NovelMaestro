    // ===== UI (SHADOW DOM) =====
    const styles = `
        <style>
            #nm-root, #nm-root * { letter-spacing: normal; word-spacing: normal; text-indent: 0; box-sizing: border-box; }
            #nm-root { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; font-size: 14px; color: #111827; }
            /* одна плавающая кнопка — меню; в покое полупрозрачна, чтобы не мешать
               чтению, полный цвет при наведении или фокусе */
            #nm-buttons { position: fixed; bottom: 20px; right: 20px; z-index: 2147483640; display: flex; gap: 8px; opacity: .55; transition: opacity .18s ease; }
            #nm-buttons:hover, #nm-buttons:focus-within { opacity: 1; }
            .nm-btn-float { background: #2563eb; color: white; border: none; padding: 12px 16px; border-radius: 8px; cursor: pointer; font-size: 20px; min-width: 50px; box-shadow: 0 4px 12px rgba(37,99,235,.3); transition: all .2s; }
            .nm-btn-float:hover { background: #1d4ed8; transform: translateY(-2px); }
            .nm-btn-float:disabled { background: #93c5fd; cursor: not-allowed; transform: none; }
            .nm-btn-float.nm-menu { background: #6b7280; }
            .nm-btn-float.nm-menu:hover { background: #4b5563; }
            .nm-menu-wrap { position: relative; }
            .nm-dropdown-menu { position: absolute; bottom: 58px; right: 0; background: white; border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,.25); padding: 6px; display: none; min-width: 240px; flex-direction: column; gap: 2px; }
            .nm-dropdown-menu.active { display: flex; }
            .nm-dropdown-item { padding: 10px 14px; border: none; background: none; text-align: left; cursor: pointer; border-radius: 6px; font-size: 14px; color: #111827; white-space: nowrap; }
            .nm-dropdown-item:hover { background: #f3f4f6; }
            .nm-modal { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.5); z-index: 2147483647; }
            .nm-modal.active { display: flex; align-items: center; justify-content: center; }
            .nm-modal-content { background: white; border-radius: 12px; max-width: 900px; width: 95%; max-height: 90vh; overflow-y: auto; padding: 24px; box-shadow: 0 20px 60px rgba(0,0,0,.3); color: #111827; }
            /* главный шелл: шапка с ✕ и подвал настроек неподвижны, скроллится только тело */
            #nm-modal .nm-modal-content { display: flex; flex-direction: column; overflow: hidden; padding: 0; }
            #nm-modal .nm-modal-header { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 16px; border-bottom: 1px solid #e5e7eb; }
            #nm-modal .nm-modal-header h2 { margin: 0; font-size: 17px; }
            #nm-modal .nm-tabs { flex: 0 0 auto; margin: 0; padding: 0 16px; }
            #nm-modal .nm-modal-body { flex: 1 1 auto; overflow-y: auto; padding: 16px 16px 0; }
            .nm-version { font-size: 12px; font-weight: 400; color: #9ca3af; margin-left: 6px; }
            /* подвал настроек: сброс и индикатор сохранения — всегда под рукой */
            .nm-settings-footer { position: sticky; bottom: 0; display: flex; align-items: center; gap: 10px; margin: 0 -16px; padding: 10px 16px; background: #fbfbfc; border-top: 1px solid #e5e7eb; }
            .nm-settings-footer .nm-status { flex: 1 1 auto; margin: 0; padding: 7px 10px; }
            .nm-settings-footer .nm-btn { flex: 0 0 auto; margin: 0 0 0 auto; }
            .nm-tabs { display: flex; border-bottom: 2px solid #e5e7eb; margin-bottom: 20px; }
            .nm-tab { padding: 10px 20px; cursor: pointer; border-bottom: 2px solid transparent; margin-bottom: -2px; }
            .nm-tab.active { border-bottom-color: #2563eb; color: #2563eb; font-weight: 600; }
            .nm-tab-content { display: none; }
            .nm-tab-content.active { display: block; }
            /* вторичные вкладки настроек: «что нужно всем» и «что нужно энтузиастам» */
            .nm-subtabs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
            .nm-subtab { padding: 6px 12px; border: 1px solid #d1d5db; border-radius: 999px; background: white; color: #374151; font-size: 13px; cursor: pointer; }
            .nm-subtab.active { background: #2563eb; border-color: #2563eb; color: white; font-weight: 600; }
            .nm-subtab-content { display: none; }
            .nm-subtab-content.active { display: block; }
            .nm-input-group { margin-bottom: 16px; }
            .nm-input-group label { display: block; margin-bottom: 6px; font-weight: 500; color: #374151; }
            .nm-input, .nm-textarea, .nm-select { width: 100%; padding: 10px; border: 1px solid #d1d5db; border-radius: 6px; font-size: 14px; background: white; color: #111827; }
            .nm-textarea { min-height: 110px; resize: vertical; font-family: Consolas, Monaco, monospace; line-height: 1.4; }
            .nm-btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; font-weight: 500; margin-right: 8px; margin-top: 8px; }
            .nm-btn-sm { padding: 6px 12px; font-size: 13px; }
            .nm-btn-primary { background: #2563eb; color: white; }
            .nm-btn-primary:hover { background: #1d4ed8; }
            .nm-btn-primary:disabled { background: #93c5fd; cursor: not-allowed; }
            .nm-btn-secondary { background: #6b7280; color: white; }
            .nm-btn-secondary:hover { background: #4b5563; }
            .nm-btn-danger { background: #dc2626; color: white; }
            .nm-btn-danger:hover { background: #b91c1c; }
            .nm-btn-success { background: #059669; color: white; }
            .nm-btn-success:hover { background: #047857; }
            .nm-glossary-table { width: 100%; border-collapse: collapse; margin-top: 8px; }
            .nm-glossary-table th { background: #f3f4f6; padding: 8px 10px; text-align: left; font-size: 12px; font-weight: 600; color: #374151; border-bottom: 2px solid #e5e7eb; cursor: pointer; user-select: none; white-space: nowrap; }
            .nm-glossary-table th:hover { background: #e5e7eb; }
            .nm-glossary-table th .nm-sort { font-size: 10px; margin-left: 4px; color: #9ca3af; }
            .nm-glossary-table th.active-sort { background: #dbeafe; color: #1e40af; }
            .nm-glossary-table th.active-sort .nm-sort { color: #2563eb; }
            .nm-glossary-table td { padding: 6px 8px; border-bottom: 1px solid #e5e7eb; vertical-align: middle; }
            .nm-glossary-table tr:hover td { background: #f9fafb; }
            .nm-glossary-table input, .nm-glossary-table select { padding: 5px 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; width: 100%; color: #111827; }
            .nm-glossary-table input:focus, .nm-glossary-table select:focus { outline: none; border-color: #2563eb; box-shadow: 0 0 0 2px rgba(37,99,235,.1); }
            .nm-delete-cell { background: #dc2626; color: white; border: none; padding: 5px 8px; border-radius: 4px; cursor: pointer; font-size: 12px; }
            .nm-count-cell { text-align: center; font-weight: 600; color: #6b7280; }
            .nm-count-cell.high { color: #059669; }
            .nm-count-cell.med { color: #d97706; }
            .nm-status { padding: 12px; border-radius: 6px; margin-top: 12px; display: none; }
            .nm-status.success { background: #d1fae5; color: #065f46; display: block; }
            .nm-status.error { background: #fee2e2; color: #991b1b; display: block; }
            .nm-status.info { background: #dbeafe; color: #1e40af; display: block; }
            .nm-close { float: right; background: none; border: none; font-size: 24px; cursor: pointer; color: #6b7280; }
            .nm-help { background: #f3f4f6; padding: 12px; border-radius: 6px; font-size: 13px; color: #6b7280; margin-bottom: 16px; }
            .nm-help code { background: rgba(128,128,128,.15); padding: 1px 4px; border-radius: 3px; }
            .nm-glossary-count { background: #2563eb; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin-left: 8px; }
            .nm-add-form { display: grid; grid-template-columns: 1fr 1fr 1.4fr auto; gap: 8px; padding: 12px; background: #eff6ff; border-radius: 6px; border: 1px solid #bfdbfe; margin-bottom: 12px; }
            .nm-add-form input, .nm-add-form select { padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; color: #111827; }
            .nm-filter-row { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
            .nm-filter-row input { flex: 1; padding: 8px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 13px; background: white; color: #111827; }
            .nm-pagination { display: flex; gap: 8px; align-items: center; justify-content: center; margin-top: 12px; flex-wrap: wrap; }
            .nm-pagination button { padding: 6px 12px; border: 1px solid #d1d5db; background: white; border-radius: 4px; cursor: pointer; font-size: 13px; color: #111827; }
            .nm-pagination button:disabled { opacity: 0.4; cursor: not-allowed; }
            .nm-pagination button.active { background: #2563eb; color: white; border-color: #2563eb; }
            .nm-pagination .nm-page-info { color: #6b7280; font-size: 13px; }
            .nm-progress-bar { width: 100%; height: 6px; background: rgba(128,128,128,.25); border-radius: 3px; overflow: hidden; }
            .nm-progress-fill { height: 100%; background: linear-gradient(90deg,#2563eb,#3b82f6); transition: width .3s; width: 0%; }
            .nm-progress-fill.retry { background: repeating-linear-gradient(45deg, #f59e0b 0 10px, #fbbf24 10px 20px); background-size: 28.3px 28.3px; animation: nm-retry-stripes .8s linear infinite; }
            @keyframes nm-retry-stripes { to { background-position: 28.3px 0; } }
            .nm-input:disabled { background: #f3f4f6; color: #9ca3af; cursor: not-allowed; }
            /* битый ввод (не число, вне диапазона, неразобранный JSON): запрос уходит
               с подменённым значением — поле обязано быть подсвечено */
            .nm-input-bad { border-color: #dc2626 !important; box-shadow: 0 0 0 2px rgba(220,38,38,.12); }
            /* чекбоксы — той же строкой, что и остальные поля: зона клика — весь текст,
               но без отдельного подсвеченного блока вокруг */
            .nm-check-row { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; cursor: pointer; color: #374151; }
            .nm-check-row input[type=checkbox] { flex: 0 0 auto; width: 16px; height: 16px; cursor: pointer; }
            /* у пункта с объяснением только что подсказка: курсор подсказывает, что она есть */
            .nm-input-group[data-tip] > label { cursor: help; }
            /* один плавающий тултип на весь шелл: вложенный в модалку обрезался бы overflow */
            #nm-tip { display: none; position: fixed; z-index: 2147483647; max-width: min(360px, calc(100vw - 24px)); padding: 8px 10px; border-radius: 8px; background: #111827; color: #f9fafb; font-size: 12.5px; line-height: 1.45; box-shadow: 0 8px 24px rgba(0,0,0,.35); pointer-events: none; white-space: pre-line; }
            #nm-tip.active { display: block; }
            .nm-section { background: #f9fafb; padding: 16px; border-radius: 8px; margin-bottom: 16px; }
            .nm-section h3 { margin: 0 0 12px 0; font-size: 16px; color: #1f2937; }
            .nm-toolbar { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
            .nm-toolbar .nm-btn { margin: 0; }
            .nm-book-info { background: #eff6ff; padding: 12px; border-radius: 6px; margin-bottom: 16px; border-left: 4px solid #2563eb; }
            .nm-url-edit { display: flex; gap: 8px; align-items: center; }
            .nm-url-edit input { flex: 1; }
            .nm-url-edit .nm-btn { margin: 0; }
            .nm-radio-group { display: flex; gap: 16px; margin-top: 8px; flex-wrap: wrap; }
            .nm-radio-group label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
            .nm-server-status { margin-top: 8px; padding: 8px 12px; border-radius: 4px; font-size: 13px; display: none; }
            .nm-server-status.show { display: block; }
            .nm-server-status.ok { background: #d1fae5; color: #065f46; }
            .nm-server-status.err { background: #fee2e2; color: #991b1b; }
            .nm-server-status.loading { background: #fef3c7; color: #92400e; }

            /* ===== ТЁМНЫЙ UI (меню, модалки, попапы) ===== */
            #nm-root.nm-ui-dark, #nm-root.nm-ui-dark .nm-modal-content { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-dropdown-menu { background: #1f232b; }
            #nm-root.nm-ui-dark .nm-dropdown-item { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-dropdown-item:hover { background: #2a2f39; }
            #nm-root.nm-ui-dark .nm-modal-content { background: #1f232b; }
            #nm-root.nm-ui-dark .nm-modal-header, #nm-root.nm-ui-dark .nm-settings-footer { border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-settings-footer { background: #1f232b; }
            #nm-root.nm-ui-dark .nm-tabs { border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-tab { color: #b9bdc6; }
            #nm-root.nm-ui-dark .nm-tab.active { color: #7fb0ff; border-bottom-color: #7fb0ff; }
            #nm-root.nm-ui-dark .nm-subtab { background: #2a2f39; color: #c6c9d0; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-subtab.active { background: #2563eb; border-color: #2563eb; color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-check-row { color: #c6c9d0; }
            #nm-root.nm-ui-dark #nm-tip { background: #0d0f13; color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-input-group label { color: #c6c9d0; }
            #nm-root.nm-ui-dark .nm-input, #nm-root.nm-ui-dark .nm-textarea, #nm-root.nm-ui-dark .nm-select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-input:disabled { background: #23272e; color: #7d828c; }
            #nm-root.nm-ui-dark .nm-section { background: #262b34; }
            #nm-root.nm-ui-dark .nm-section h3 { color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-help { background: #262b34; color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-add-form { background: #232833; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-add-form input, #nm-root.nm-ui-dark .nm-add-form select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-filter-row input { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-glossary-table th { background: #2a2f39; color: #c6c9d0; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-glossary-table th:hover { background: #31363f; }
            #nm-root.nm-ui-dark .nm-glossary-table th.active-sort { background: #1c2c4a; color: #a8c6ff; }
            #nm-root.nm-ui-dark .nm-glossary-table td { border-color: #31363f; }
            #nm-root.nm-ui-dark .nm-glossary-table tr:hover td { background: #262b34; }
            #nm-root.nm-ui-dark .nm-glossary-table input, #nm-root.nm-ui-dark .nm-glossary-table select { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-count-cell { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-pagination button { background: #2a2f39; color: #e2e2dc; border-color: #3a3f4a; }
            #nm-root.nm-ui-dark .nm-pagination button.active { background: #2563eb; color: white; }
            #nm-root.nm-ui-dark .nm-pagination .nm-page-info { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-book-info { background: #232833; }
            #nm-root.nm-ui-dark .nm-close { color: #9aa0aa; }
            #nm-root.nm-ui-dark .nm-status.success { background: #123524; color: #7ee2b8; }
            #nm-root.nm-ui-dark .nm-status.error { background: #3d1d1d; color: #f3b4b4; }
            #nm-root.nm-ui-dark .nm-status.info { background: #1c2c4a; color: #a8c6ff; }
            #nm-root.nm-ui-dark .nm-server-status.ok { background: #123524; color: #7ee2b8; }
            #nm-root.nm-ui-dark .nm-server-status.err { background: #3d1d1d; color: #f3b4b4; }
            #nm-root.nm-ui-dark .nm-server-status.loading { background: #3d3116; color: #e8c37a; }
            #nm-root.nm-ui-dark .nm-training-instructions, #nm-root.nm-ui-dark .nm-training-popup { background: #1f232b; color: #e2e2dc; }
            #nm-root.nm-ui-dark .nm-training-popup h4 { color: #c6c9d0; }

            /* ===== ЧИТАЛКА ===== */
            #nm-reader-mode { display: none; position: fixed; inset: 0; z-index: 2147483640; overflow-y: auto; overflow-x: hidden; }
            #nm-reader-mode.active { display: block; }
            #nm-reader-mode.nm-reader-light { background: #faf7f0; color: #26221c; }
            #nm-reader-mode.nm-reader-dark { background: #16181d; color: #d8d8d3; }
            .nm-reader-topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 5; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 16px; backdrop-filter: blur(6px); }
            #nm-reader-mode.nm-reader-light .nm-reader-topbar { background: rgba(250,247,240,.92); border-bottom: 1px solid #e5ded2; }
            #nm-reader-mode.nm-reader-dark .nm-reader-topbar { background: rgba(22,24,29,.92); border-bottom: 1px solid #2a2d35; }
            .nm-reader-title { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
            .nm-reader-topbar-buttons { display: flex; gap: 6px; flex-shrink: 0; }
            .nm-reader-topbar-buttons button { border: none; border-radius: 6px; cursor: pointer; font-size: 15px; padding: 6px 10px; }
            #nm-reader-mode.nm-reader-light .nm-reader-topbar-buttons button { background: #e8e2d6; color: #26221c; }
            #nm-reader-mode.nm-reader-dark .nm-reader-topbar-buttons button { background: #2a2d35; color: #d8d8d3; }
            .nm-reader-menu-panel { display: none; position: absolute; top: calc(100% + 2px); right: 10px; flex-direction: column; gap: 4px; padding: 8px; border-radius: 10px; z-index: 6; min-width: 210px; }
            .nm-reader-menu-panel.active { display: flex; }
            .nm-reader-menu-panel button { border: none; border-radius: 6px; cursor: pointer; font-size: 14px; padding: 8px 10px; text-align: left; white-space: nowrap; }
            #nm-reader-mode.nm-reader-light .nm-reader-menu-panel { background: rgba(250,247,240,.97); border: 1px solid #e5ded2; box-shadow: 0 6px 20px rgba(0,0,0,.15); }
            #nm-reader-mode.nm-reader-dark .nm-reader-menu-panel { background: rgba(22,24,29,.97); border: 1px solid #2a2d35; box-shadow: 0 6px 20px rgba(0,0,0,.4); }
            #nm-reader-mode.nm-reader-light .nm-reader-menu-panel button { background: #e8e2d6; color: #26221c; }
            #nm-reader-mode.nm-reader-dark .nm-reader-menu-panel button { background: #2a2d35; color: #d8d8d3; }
            .nm-current-only { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: #6b7280; white-space: nowrap; cursor: pointer; }
            .nm-reader-content { margin: 0 auto; padding: 70px 20px 150px; max-width: var(--nm-content-width, 66%); }
            .nm-reader-content p { text-align: justify; }
            .nm-reader-loading { text-align: center; padding: 60px 0; font-size: 16px; opacity: .7; }
            .nm-reader-bottombar { position: fixed; bottom: 0; left: 0; right: 0; z-index: 5; display: flex; flex-direction: column; gap: 8px; align-items: center; padding: 10px 14px 12px; backdrop-filter: blur(6px); }
            #nm-reader-mode.nm-reader-light .nm-reader-bottombar { background: rgba(250,247,240,.92); border-top: 1px solid #e5ded2; }
            #nm-reader-mode.nm-reader-dark .nm-reader-bottombar { background: rgba(22,24,29,.92); border-top: 1px solid #2a2d35; }
            .nm-reader-progress { width: min(680px, 94%); display: none; flex-direction: column; gap: 6px; font-size: 13px; }
            .nm-reader-progress.active { display: flex; }
            .nm-rp-row { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
            #reader-progress-title { font-weight: 600; }
            #reader-cancel, #reader-progress-close { border: 1px solid rgba(200,80,80,.6); color: #b3403a; background: transparent; border-radius: 6px; padding: 4px 12px; cursor: pointer; font-size: 12px; }
            #reader-progress-close { border-color: rgba(128,128,128,.5); color: inherit; }
            #nm-reader-mode.nm-reader-dark #reader-cancel { color: #e08585; border-color: rgba(224,133,133,.5); }
            #reader-cancel:hover { background: rgba(200,80,80,.12); }
            #reader-progress-close:hover { background: rgba(128,128,128,.15); }
            #reader-progress-status { opacity: .75; font-size: 12px; }
            .nm-reader-nav { display: flex; gap: 10px; justify-content: center; align-items: center; flex-wrap: wrap; }
            .nm-reader-nav button { padding: 9px 20px; border-radius: 8px; cursor: pointer; font-size: 14px; font-weight: 500; background: transparent; border: 1px solid; transition: background .15s; }
            #nm-reader-mode.nm-reader-light .nm-reader-nav button { color: #4a443b; border-color: #d3cabb; background: rgba(255,255,255,.45); }
            #nm-reader-mode.nm-reader-light .nm-reader-nav button:hover { background: #efe9dd; }
            #nm-reader-mode.nm-reader-dark .nm-reader-nav button { color: #c6c6bf; border-color: #3a3d46; background: rgba(255,255,255,.04); }
            #nm-reader-mode.nm-reader-dark .nm-reader-nav button:hover { background: #23262e; }
            .nm-reader-nav button:disabled { opacity: .35; cursor: not-allowed; }
            #reader-preload-status { font-size: 12px; opacity: .65; display: none; }

            /* ===== ОБУЧЕНИЕ ===== */
            #nm-element-training { display: none; position: fixed; inset: 0; z-index: 2147483645; pointer-events: none; }
            #nm-element-training.active { display: block; }
            .nm-training-instructions { pointer-events: auto; position: fixed; top: 16px; left: 50%; transform: translateX(-50%); background: white; color: #111827; padding: 14px 20px; border-radius: 10px; box-shadow: 0 6px 24px rgba(0,0,0,.35); font-size: 13px; max-width: 640px; text-align: center; z-index: 10; }
            .nm-training-instructions h3 { margin: 0 0 6px 0; font-size: 15px; }
            .nm-training-instructions .nm-btn { margin-top: 10px; }
            .nm-training-popup { pointer-events: auto; position: fixed; background: white; color: #111827; padding: 14px; border-radius: 10px; box-shadow: 0 8px 28px rgba(0,0,0,.35); z-index: 11; display: none; min-width: 230px; }
            .nm-training-popup.active { display: block; }
            .nm-training-popup h4 { margin: 0 0 10px 0; font-size: 13px; color: #374151; }
            .nm-training-buttons { display: flex; flex-direction: column; gap: 6px; }
            .nm-training-buttons button { padding: 9px 14px; border: none; border-radius: 6px; cursor: pointer; font-size: 13px; text-align: left; color: white; }
            .nm-training-buttons button:hover { opacity: .9; }

            /* ===== МОБИЛЬНАЯ АДАПТАЦИЯ =====
               указатель coarse ловит телефон даже в режиме «полной версии сайта» */
            @media (max-width: 768px), (pointer: coarse) {
                #nm-buttons { bottom: 12px; right: 12px; bottom: calc(12px + env(safe-area-inset-bottom)); }
                /* меньше стала, но осталась в пределах касания (44px) */
                .nm-btn-float { min-width: 44px; min-height: 44px; font-size: 20px; padding: 10px 12px; }
                .nm-dropdown-menu { min-width: 0; width: min(320px, calc(100vw - 24px)); bottom: 66px; }
                .nm-dropdown-item { white-space: normal; padding: 12px 14px; }
                .nm-modal.active { align-items: stretch; justify-content: stretch; }
                .nm-modal-content { width: 100%; max-width: 100%; height: 100%; max-height: 100%; border-radius: 0; padding: 14px; padding: calc(14px + env(safe-area-inset-top)) 14px calc(14px + env(safe-area-inset-bottom)); }
                #nm-modal .nm-modal-content { padding: 0; }
                #nm-modal .nm-modal-header { padding: calc(10px + env(safe-area-inset-top)) 12px 10px; }
                #nm-modal .nm-tabs { padding: 0 12px; }
                #nm-modal .nm-modal-body { padding: 12px 12px 0; }
                .nm-settings-footer { margin: 0 -12px; padding: 10px 12px calc(10px + env(safe-area-inset-bottom)); }
                .nm-tabs { overflow-x: auto; flex-wrap: nowrap; }
                .nm-tab { flex-shrink: 0; white-space: nowrap; padding: 10px 14px; }
                .nm-subtab { min-height: 36px; padding: 8px 14px; }
                .nm-check-row input[type=checkbox] { width: 18px; height: 18px; }
                .nm-input, .nm-textarea, .nm-select, .nm-glossary-table input, .nm-glossary-table select, .nm-add-form input, .nm-add-form select, .nm-filter-row input { font-size: 16px; }
                .nm-add-form { grid-template-columns: 1fr; }
                #glossary-list { overflow-x: auto; -webkit-overflow-scrolling: touch; }
                .nm-glossary-table { min-width: 620px; }
                .nm-reader-topbar { padding: 8px 10px; padding: calc(8px + env(safe-area-inset-top)) 10px 8px; }
                .nm-reader-bottombar { padding: 8px 10px 10px; padding: 8px 10px calc(10px + env(safe-area-inset-bottom)); }
                #nm-reader-mode .nm-reader-content { max-width: 100%; padding: 60px 12px 120px; padding-top: calc(60px + env(safe-area-inset-top)); }
                .nm-reader-nav { gap: 6px; }
                /* на телефоне панель — компактнее и только иконками (подписи скрыты) */
                .nm-reader-nav button { min-height: 40px; padding: 8px 14px; font-size: 15px; flex: 0 1 auto; }
                .nm-reader-nav .nm-nav-label { display: none; }
                .nm-training-instructions { max-width: calc(100vw - 16px); top: 8px; padding: 8px 10px; font-size: 11px; }
                .nm-training-instructions h3 { font-size: 13px; margin-bottom: 4px; }
                .nm-training-instructions .nm-btn { padding: 7px 12px; font-size: 12px; margin-top: 6px; }
                .nm-training-popup { max-width: calc(100vw - 16px); min-width: 200px; }
                .nm-training-buttons button { padding: 9px 10px; font-size: 12px; }
            }
        </style>
    `;

    (function injectPageTrainStyle() {
        if (document.getElementById('nm-page-train-style')) return;
        const st = document.createElement('style');
        st.id = 'nm-page-train-style';
        st.textContent = `
            .nm-training-highlight { outline: 3px solid #2563eb !important; outline-offset: 2px; background-color: rgba(37,99,235,.12) !important; cursor: crosshair !important; }
            .nm-training-picked { outline: 3px solid #059669 !important; outline-offset: 2px; }
            body.nm-training-on { -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
        `;
        // head в некоторых средах раннего исполнения ещё не создан — фолбэк на
        // documentElement: без стиля не останется, но и падать до разметки UI нельзя
        (document.head || document.documentElement).appendChild(st);
    })();
