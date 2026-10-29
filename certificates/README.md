# Сертификаты для HTTPS Росстата

Росстат использует сертификат, выпущенный Russian Trusted Sub CA. Сервер не
передаёт промежуточный сертификат; стандартный CA bundle Python не содержит
нужный корень. `rosstat_ca_bundle.pem` содержит два публичных сертификата:

- Корень: https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
- Выпускающий 2024: https://gu-st.ru/content/lending/russian_trusted_sub_ca_2024_pem.crt

Загружены по HTTPS с проверкой сертификата 29.10.2026. Источник:
[страница Госуслуг](https://www.gosuslugi.ru/tls).

SHA-256 отпечатки DER:

- Корень: `D26D2D0231B7C39F92CC738512BA54103519E4405D68B5BD703E9788CA8ECF31`
- Выпускающий: `2155785036C900DBB5F1BB2A1569C80C55595BD6BF94867A29BBDDBC7D88A3F2`

Bundle применяется только в сессии загрузчика Росстата. TLS-проверка остаётся
включённой. Системное хранилище не изменяется. `REQUESTS_CA_BUNDLE` или
`CURL_CA_BUNDLE`, если заданы пользователем, имеют приоритет.
