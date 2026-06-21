# QVM Screener

Quality · Value · Momentum скрининг акций US-рынка. Развёртывается на Vercel,
данные берутся из Financial Modeling Prep (FMP).

## Структура

```
qvm-screener/
├── api/
│   └── screen.py        serverless-функция (Python, без pandas)
├── public/
│   └── index.html       минимальный фронтенд
├── vercel.json          конфиг (maxDuration)
├── requirements.txt     зависимостей нет (только stdlib)
└── README.md
```

Архитектура простая: фронтенд (`public/index.html`) дёргает эндпоинт
`/api/screen`, тот опрашивает FMP по каждому тикеру, прогоняет три фильтра
(Quality → Value → Momentum) и возвращает JSON. Никакой торговой
автоматизации — только отбор кандидатов.

## Почему FMP, а не yfinance

yfinance скрейпит Yahoo и с дата-центровых IP Vercel регулярно ловит блокировки
и пустые ответы. FMP отдаёт чистый JSON, работает стабильно из облака, и у него
есть готовый эндпоинт `stock-price-change` с 3M/6M momentum одним запросом.

## Деплой через Git + Vercel

1. **Получи ключ FMP**
   Зарегистрируйся на financialmodelingprep.com, возьми API-ключ
   (есть бесплатный тариф с лимитом запросов в день).

2. **Залей проект в Git**
   ```bash
   cd qvm-screener
   git init
   git add .
   git commit -m "QVM screener"
   git remote add origin <твой-репозиторий>
   git push -u origin main
   ```

3. **Подключи к Vercel**
   - vercel.com → Add New → Project → импортируй репозиторий из Git
   - Framework Preset: Other (Vercel сам найдёт `api/` и `public/`)
   - Deploy

4. **Добавь ключ как переменную окружения**
   - В проекте на Vercel: Settings → Environment Variables
   - Name: `FMP_API_KEY`, Value: твой ключ
   - Сохрани и сделай Redeploy (переменная подхватывается при сборке)

5. **Готово**
   Открываешь URL проекта — там форма ввода тикеров. Дальнейшие push в Git
   автоматически передеплоиваются.

## Локальная проверка перед деплоем

```bash
npm i -g vercel
vercel dev          # поднимет и фронт, и функцию локально
```
Не забудь создать `.env` с `FMP_API_KEY=...` (и добавить `.env` в `.gitignore`).

## Что осталось доработать

- **Эндпоинты FMP**: у FMP была миграция API (`/api/v3/` → `/stable/`). Перед
  деплоем сверь URL в `FMP_BASE` и полях ответа с актуальной документацией —
  имена полей (`operatingIncome`, `freeCashFlow` и т.д.) могли измениться.
- **Лимиты запросов FMP**: бесплатный тариф ограничен по числу запросов в день.
  Каждый тикер = ~5 запросов. Для широкого юниверса смотри платный тариф или
  bulk-эндпоинты FMP (один запрос на много компаний).
- **Финальное ранжирование**: сейчас сортировка по Earnings Yield. В оригинальной
  методике дю Туа — Value Composite One (комбинация нескольких value-метрик).
- **Кэширование**: фундаментал меняется раз в квартал — есть смысл кэшировать
  ответы FMP, чтобы не жечь лимит при каждом сканировании.
