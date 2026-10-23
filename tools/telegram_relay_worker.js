// «Мостик» к Telegram для сервера, у которого api.telegram.org заблокирован.
// Cloudflare Worker (бесплатно): сервер приложения шлёт запрос сюда, Worker передаёт его в Telegram и возвращает ответ.
//
// Как поставить: dash.cloudflare.com → Workers & Pages → Create → Worker → вставить этот код → заменить СЕКРЕТ ниже на свою
// длинную случайную строку (только латинские буквы и цифры) → Deploy. Адрес мостика, который нужно дать утилите:
//   https://<имя>.workers.dev/<ТОТ ЖЕ СЕКРЕТ>
// Секрет в адресе нужен, чтобы мостиком не мог пользоваться кто угодно: без него Worker отвечает 404.
const SECRET = "ЗАМЕНИТЕ_НА_ДЛИННУЮ_СЛУЧАЙНУЮ_СТРОКУ";

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const prefix = "/" + SECRET;
    if (SECRET.startsWith("ЗАМЕНИТЕ") || !url.pathname.startsWith(prefix + "/")) {
      return new Response("not found", { status: 404 });
    }
    url.hostname = "api.telegram.org";
    url.protocol = "https:";
    url.port = "";
    url.pathname = url.pathname.slice(prefix.length);
    return fetch(new Request(url.toString(), request));
  },
};
