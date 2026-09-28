
    const host = document.createElement('div');
    host.id = 'nm-lite-host';
    document.documentElement.appendChild(host);
    const shadow = host.attachShadow({ mode: 'open' });
    const uiFrag = document.createRange().createContextualFragment(`
        ${styles}
        <div id="nm-root">
            <div id="nm-buttons">
                <div class="nm-menu-wrap">
                    <button class="nm-btn-float nm-menu" id="btn-menu" title="NovelMaestro Lite">⋮</button>
                    <div class="nm-dropdown-menu" id="dropdown-menu">
                        <button class="nm-dropdown-item" id="btn-translate">🌐 Перевести / читать</button>
                        <button class="nm-dropdown-item" id="btn-book-menu">➕ Добавить книгу</button>
                        <button class="nm-dropdown-item" id="btn-settings-menu">⚙️ Настройки</button>
                        <button class="nm-dropdown-item" id="btn-extract-menu">✨ Извлечь термины вручную</button>
                        <button class="nm-dropdown-item" id="btn-train-menu">🎯 Обучить элементам</button>
                        <button class="nm-dropdown-item" id="btn-theme-menu">🌓 Тема</button>
                    </div>
                </div>
            </div>

            <div class="nm-modal" id="nm-modal">
                <div class="nm-modal-content">
                    <button class="nm-close" id="nm-close">&times;</button>
                    <h2 style="margin-top:0;">NovelMaestro Lite <span style="font-size:12px;color:#9ca3af;font-weight:400;">v${APP_VERSION}</span></h2>
                    <div class="nm-tabs">
                        <div class="nm-tab active" data-tab="book">📚 Книга</div>
                        <div class="nm-tab" data-tab="glossary">✨ Глоссарий <span class="nm-glossary-count" id="glossary-count">0</span></div>
                        <div class="nm-tab" data-tab="settings">⚙️ Настройки</div>
                    </div>
                    <div class="nm-tab-content active" id="tab-book">
                        <div class="nm-section">
                            <h3>Управление книгами</h3>
                            <div class="nm-input-group">
                                <label>Выбрать книгу:</label>
                                <select class="nm-select" id="book-select"></select>
                            </div>
                            <div id="book-manage-area"></div>
                        </div>
                        <div class="nm-status" id="status-book"></div>
                    </div>
                    <div class="nm-tab-content" id="tab-glossary">
                        <div class="nm-help" id="glossary-book-hint" style="display:none;">📚 У каждой книги свой глоссарий; он хранится в памяти браузера для сайта книги. Откройте страницу книги — она определяется по заданному URL (например <code>…/fiction/58180/…</code>) — и глоссарий появится здесь.</div>
                        <div id="glossary-body">
                        <div class="nm-add-form">
                            <input type="text" id="new-term" placeholder="Термин">
                            <input type="text" id="new-translation" placeholder="Перевод">
                            <input type="text" id="new-type" list="nm-type-list" placeholder="Тип (Person (male), Location…)" value="Person (male)">
                            <datalist id="nm-type-list"></datalist>
                            <button class="nm-btn nm-btn-primary" id="btn-add-term" style="margin:0;">+ Добавить</button>
                        </div>
                        <div class="nm-toolbar">
                            <button class="nm-btn nm-btn-success" id="btn-extract-terms">✨ Извлечь термины со страницы</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-import">📥 Импорт</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-export">📤 Экспорт</button>
                            <button class="nm-btn nm-btn-danger" id="btn-clear-glossary">🗑 Очистить</button>
                        </div>
                        <div class="nm-filter-row">
                            <input type="text" id="glossary-filter" placeholder="🔍 Фильтр по термину, переводу или типу...">
                        </div>
                        <div id="glossary-list"></div>
                        <div class="nm-pagination" id="glossary-pagination"></div>
                        <div class="nm-status" id="status-glossary"></div>
                        </div>
                    </div>
                    <div class="nm-tab-content" id="tab-settings">
                        <div class="nm-section">
                            <h3>🤖 API</h3>
                            <div class="nm-input-group"><label>API Host:</label><input type="text" class="nm-input" id="api-host"></div>
                            <div class="nm-input-group"><label>API Key:</label><input type="password" class="nm-input" id="api-key">
                                <small>Пустой API Key разрешён при включённом чекбоксе ниже.</small>
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="local-model">
                                <label for="local-model">🖥️ Локальная модель без API-ключа</label>
                            </div>
                            <div class="nm-input-group"><label>Модель:</label><input type="text" class="nm-input" id="model"></div>
                            <div class="nm-input-group"><label>Уровень reasoning:</label>
                                <input type="text" class="nm-input" id="reasoning-effort" list="nm-reasoning-list" placeholder="None">
                                <datalist id="nm-reasoning-list">
                                    <option value="None"></option><option value="minimal"></option><option value="low"></option>
                                    <option value="medium"></option><option value="high"></option>
                                </datalist>
                                <small>Пусто или 'None' — параметр не передаётся.</small>
                            </div>
                            <button class="nm-btn nm-btn-sm nm-btn-primary" id="btn-check-server">🔌 Проверить сервер</button>
                            <div class="nm-server-status" id="server-status"></div>
                        </div>
                        <div class="nm-section">
                            <h3>📝 Перевод</h3>
                            <div class="nm-input-group"><label>Исходный язык:</label>
                                <select class="nm-select" id="source-lang">
                                    <option value="Авто">Авто</option><option value="Китайский">Китайский</option>
                                    <option value="Английский">Английский</option><option value="Японский">Японский</option>
                                    <option value="Корейский">Корейский</option><option value="Русский">Русский</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Целевой язык:</label>
                                <select class="nm-select" id="target-lang">
                                    <option value="Русский">Русский</option><option value="Английский">Английский</option>
                                    <option value="Испанский">Испанский</option><option value="Французский">Французский</option>
                                    <option value="Немецкий">Немецкий</option><option value="Японский">Японский</option>
                                    <option value="Корейский">Корейский</option><option value="Китайский">Китайский</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Размер чанка (символов):</label>
                                <input type="number" class="nm-input" id="chunk-size" min="1000" max="100000" step="1000">
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="preemptive-translate">
                                <label for="preemptive-translate">🚀 Автоперевод следующей главы в фоне</label>
                            </div>
                            <small>Переведённые главы (текущая и следующая) кэшируются в памяти браузера этого сайта — из них работают мгновенное открытие с кэшированной главы и экспорт TXT.</small>
                        </div>
                        <div class="nm-section">
                            <h3>🌐 Сеть</h3>
                            <div class="nm-input-group"><label>Таймаут (с):</label>
                                <input type="number" class="nm-input" id="request-timeout" min="0" step="1">
                                <small>СЕК. 0 = без таймаута. При стриминге это пауза между токенами: ни одного символа за это время — запрос считается зависшим и повторяется. У запроса без стрима (например, «Проверить сервер») это ожидание всего ответа: локальная модель на телефоне легко думает дольше 10 секунд. По умолчанию 60.</small>
                            </div>
                            <div class="nm-input-group"><label>Количество ретраев при ошибке:</label>
                                <input type="number" class="nm-input" id="max-retries" min="0" max="10">
                                <small>Повторные попытки при сетевых ошибках, таймаутах и зависании стриминга (не при HTTP 4xx/5xx).</small>
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>🔍 Глоссарий</h3>
                            <div class="nm-input-group"><label>Порог нечеткого поиска (0.0 - 1.0):</label>
                                <input type="number" class="nm-input" id="fuzzy-threshold" min="0" max="1" step="0.05">
                            </div>
                            <div class="nm-checkbox-group">
                                <input type="checkbox" id="auto-ner">
                                <label for="auto-ner">Автоизвлечение терминов (один раз на страницу)</label>
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>📖 Читалка</h3>
                            <div class="nm-input-group"><label>Тема:</label>
                                <select class="nm-select" id="reader-theme">
                                    <option value="light">☀️ Светлая</option>
                                    <option value="dark">🌙 Тёмная</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Шрифт:</label>
                                <select class="nm-select" id="reader-font-family">
                                    <option value="Georgia, serif">Georgia (serif)</option>
                                    <option value="Arial, sans-serif">Arial (sans-serif)</option>
                                    <option value="'Times New Roman', serif">Times New Roman</option>
                                    <option value="Verdana, sans-serif">Verdana</option>
                                    <option value="'Segoe UI', sans-serif">Segoe UI</option>
                                </select>
                            </div>
                            <div class="nm-input-group"><label>Размер шрифта (px):</label>
                                <input type="number" class="nm-input" id="reader-font-size" min="12" max="32" step="1">
                            </div>
                            <div class="nm-input-group"><label>Межстрочный интервал:</label>
                                <input type="number" class="nm-input" id="reader-line-height" min="1" max="3" step="0.1">
                            </div>
                            <div class="nm-input-group"><label>Отступ между абзацами (em):</label>
                                <input type="number" class="nm-input" id="reader-paragraph-spacing" min="0.2" max="4" step="0.1">
                            </div>
                            <div class="nm-input-group"><label>Ширина колонки (% экрана):</label>
                                <input type="number" class="nm-input" id="reader-content-width" min="30" max="100" step="5">
                            </div>
                        </div>
                        <div class="nm-section">
                            <h3>💬 Промпты</h3>
                            <div class="nm-input-group"><label>Промпт перевода ({sourceLang}, {targetLang}, {glossary}, {text}):</label>
                                <textarea class="nm-textarea" id="translation-prompt"></textarea>
                            </div>
                            <div class="nm-input-group"><label>Промпт извлечения терминов ({targetLang}, {text}):</label>
                                <textarea class="nm-textarea" id="extraction-prompt"></textarea>
                            </div>
                        </div>
                        <div class="nm-help" style="margin-bottom:8px;">ℹ️ Настройки сохраняются автоматически при каждом изменении.</div>
                        <button class="nm-btn nm-btn-secondary" id="btn-reset-settings">Сбросить настройки</button>
                        <div class="nm-status" id="status-settings"></div>
                        <div class="nm-backup-section">
                            <h3>💾 Полный бэкап</h3>
                            <p style="font-size:13px;color:#6b7280;margin:0 0 8px 0;">Экспортирует/импортирует <b>все</b> данные: книги с глоссариями, кэш NER, глобальный глоссарий, настройки.</p>
                            <button class="nm-btn nm-btn-secondary" id="btn-full-export">📤 Экспорт всех данных</button>
                            <button class="nm-btn nm-btn-secondary" id="btn-full-import">📥 Импорт всех данных</button>
                            <div class="nm-status" id="status-backup"></div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="nm-modal" id="nm-book-modal">
                <div class="nm-modal-content" style="max-width:640px;">
                    <h2 style="margin-top:0;">📚 Определение книги</h2>
                    <div class="nm-help">
                        Укажите <b>постоянную часть URL книги</b> — ту, которая НЕ меняется при переходе от главы к главе.<br><br>
                        Примеры:<br>
                        • <code>…/fiction/58180/death-after-death-…/chapter/982968/ch-01-…</code> → <code>https://www.royalroad.com/fiction/58180/death-after-death-roguelike-isekai</code><br>
                        • <code>…/n/cp61433/cpplpnhk?chapterNumber=3</code> → <code>https://czbooks.net/n/cp61433/cpplpnhk</code><br>
                        • <code>…/txt/88724/41021619</code> и <code>…/txt/88724/41021865</code> → <code>https://www.69shuba.com/txt/88724</code>
                    </div>
                    <div class="nm-input-group"><label>URL книги:</label>
                        <div class="nm-url-edit">
                            <input type="text" class="nm-input" id="book-modal-url">
                            <button class="nm-btn nm-btn-secondary" id="btn-autofill-url">🔍 Авто</button>
                        </div>
                    </div>
                    <div class="nm-input-group"><label>Название книги:</label>
                        <input type="text" class="nm-input" id="book-modal-name">
                    </div>
                    <button class="nm-btn nm-btn-primary" id="btn-save-new-book">💾 Сохранить</button>
                    <button class="nm-btn nm-btn-secondary" id="btn-cancel-new-book">Отмена</button>
                </div>
            </div>

            <!-- ЧИТАЛКА -->
            <div id="nm-reader-mode">
                <div class="nm-reader-topbar">
                    <div class="nm-reader-title" id="reader-title"></div>
                    <div class="nm-reader-topbar-buttons">
                        <button id="reader-retranslate" title="Перевести текущую главу заново (игнорирует кэш)">🌐</button>
                        <button id="reader-theme-toggle" title="Сменить тему">🌓</button>
                        <button id="reader-settings" title="Настройки">⚙️</button>
                        <button id="reader-close" title="Закрыть читалку">✕</button>
                    </div>
                </div>
                <div class="nm-reader-content" id="reader-content"></div>
                <div class="nm-reader-bottombar">
                    <div class="nm-reader-progress" id="reader-progress">
                        <div class="nm-rp-row">
                            <span id="reader-progress-title">🔄 Перевод...</span>
                            <button id="reader-cancel">Отменить</button>
                        </div>
                        <div class="nm-progress-bar"><div class="nm-progress-fill" id="reader-progress-fill"></div></div>
                        <div id="reader-progress-status">Подготовка...</div>
                    </div>
                    <div class="nm-reader-nav">
                        <button id="reader-prev" title="Предыдущая глава">←<span class="nm-nav-label"> Предыдущая</span></button>
                        <button id="reader-toc" title="Оглавление">☰<span class="nm-nav-label"> Оглавление</span></button>
                        <button id="reader-next" title="Следующая глава">→<span class="nm-nav-label"> Следующая</span></button>
                        <span id="reader-preload-status"></span>
                    </div>
                </div>
            </div>

            <!-- ОБУЧЕНИЕ -->
            <div id="nm-element-training">
                <div class="nm-training-instructions">
                    <h3>🎯 Режим обучения элементам</h3>
                    <div>
                        Наведите курсор на элемент и кликните по нему, затем выберите тип:<br>
                        <span id="nm-touch-hint" style="display:none;">📱 На телефоне: одиночный тап — только подсветка; <b>двойной тап или удержание ~0,5 с</b> — выбор.<br></span>
                        <b>📄 Блок текста</b> (обязательно) • <b>← Назад</b> • <b>→ Вперёд</b> • <b>☰ Оглавление</b> (необязательно).<br>
                        Обучение работает как эвристика на всю книгу: на других главах элементы будут найдены по структуре страницы.<br>
                        Когда закончите — нажмите «✅ Готово».
                    </div>
                    <button class="nm-btn nm-btn-success" id="btn-finish-training">✅ Готово</button>
                    <button class="nm-btn nm-btn-danger" id="btn-cancel-training">Отмена</button>
                </div>
                <div class="nm-training-popup" id="training-popup">
                    <h4>Назначить тип элемента:</h4>
                    <div class="nm-training-buttons">
                        <button style="background:#2563eb;" data-type="content">📄 Блок основного текста</button>
                        <button style="background:#059669;" data-type="prev">← Кнопка «Назад»</button>
                        <button style="background:#059669;" data-type="next">Кнопка «Вперёд» →</button>
                        <button style="background:#d97706;" data-type="toc">☰ Кнопка «Оглавление»</button>
                        <button style="background:#6b7280;" id="btn-training-cancel-pick">Отмена выбора</button>
                    </div>
                </div>
            </div>
        </div>
    `);
    shadow.appendChild(uiFrag);
