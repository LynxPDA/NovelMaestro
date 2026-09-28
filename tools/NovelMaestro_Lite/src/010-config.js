    const APP_VERSION = '{{VERSION}}';

    // ===== КОНФИГУРАЦИЯ =====
    const DEFAULT_CONFIG = {
        apiHost: 'https://routerai.ru/api/v1',
        apiKey: '',
        model: 'google/gemma-4-31b-it',
        sourceLang: 'Авто',
        targetLang: 'Русский',
        reasoningEffort: 'None',
        chunkSize: 30000,
        requestTimeout: 60, // СЕКУНДЫ (0 = без таймаута): у стрима — пауза между токенами, у обычного запроса — ожидание всего ответа
        maxRetries: 3,
        localModel: false,
        gmTransport: 'auto',
        translationPrompt: 'Переведи следующий текст с {sourceLang} на {targetLang}.\n\nГЛОССАРИЙ ТЕРМИНОВ (обязательно используй эти переводы, сохраняй пол персонажей):\n{glossary}\n\nВАЖНО:\n- Имена и термины переводи точно по глоссарию\n- Сохраняй пол персонажей (он/она) согласно глоссарию\n- Сохраняй стиль оригинала\n- Сохраняй разбивку на абзацы\n- Возвращай ТОЛЬКО перевод, без комментариев\n\nТекст:\n{text}',
        extractionPrompt: 'Извлеки из текста имена персонажей, места, артефакты, организации и важные термины. Перевод терминов должен быть на {targetLang}.\n\nВерни JSON в формате:\n{\n  "term": "оригинальный термин",\n  "translation": "перевод на {targetLang}. Только 1 вариант перевода!",\n  "type": "Тип записи (Пример: Person (male), Creature (female), Location, Artifact, Organization, Term)"\n}\n\ntype - тип записи. Для живых существ (персонажи, существа) указывай пол в скобках:\n- Person (male) / Person (female) — персонаж мужского/женского пола\n- Person (unknown) — пол неизвестен\n- Creature (male) / Creature (female) — существо\nДля не-персонажей пол не указывай: Location, Artifact, Organization, Term и т.п.\n\nВерни ТОЛЬКО валидный JSON массив объектов. Без дополнительного текста.\n\nТекст:\n{text}',
        fuzzySearchThreshold: 0.7,
        autoNER: true,
        preemptiveTranslation: true, // автоперевод следующей главы в фоне
        // 'auto' — следовать системной теме; 'dark'/'light' — ручной выбор кнопкой в читалке
        readerTheme: 'auto',
        readerFontFamily: 'Georgia, serif',
        readerFontSize: 14,
        readerLineHeight: 1.6,
        readerParagraphSpacing: 1.2,
        readerContentWidth: 80
    };

    // Тема интерфейса и читалки: по умолчанию «как в системе», кнопка в читалке ходит
    // по кольцу auto → тёмная → светлая → auto.
    const THEME_MODE_LABELS = { auto: 'как в системе', dark: 'тёмная', light: 'светлая' };
    const THEME_MODE_CYCLE = { auto: 'dark', dark: 'light', light: 'auto' };

    // В GM-хранилище расширения — только список книг и настройки: глоссарии
    // (мегабайты) и кэш переводов живут в IndexedDB каждого сайта отдельно.
    let config = { ...DEFAULT_CONFIG, ...GM_getValue('config', {}) };
    let books = GM_getValue('books', {});
    let currentBookKey = null;
    let managedBookKey = null;

    let glossarySort = { field: 'count', dir: 'desc' };
    let glossaryPage = 0;
    const PAGE_SIZE = 25;
    let glossaryFilter = '';

    const ngramCache = new Map();
    const MAX_CACHE_SIZE = 1000;

    let readerModeActive = false;
    let readerState = null;
    let elementTrainingMode = false;
    let pendingTranslateAfterTraining = false;
    let trainingHighlightedEl = null;
    let trainingPopupTarget = null;
    // обучение на тач-устройствах — отдельный touch-конвейер: отменяемый touchend
    // подавляет синтетические click/переход по ссылке и long-press-меню браузера,
    // одиночный тап только подсвечивает, двойной тап или удержание открывают попап
    let trainTapEl = null;
    let trainTapTime = 0;
    let trainTouchT = 0;
    let trainTouchXY = null;
    let trainTouchHandledAt = 0;
    const DOUBLE_TAP_DELAY = 350;
    const LONG_PRESS_DELAY = 450;
    const preemptiveRunning = new Set();

