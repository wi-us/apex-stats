import { createFileRoute } from "@tanstack/react-router";

export const Route = createFileRoute("/docs")({
  component: DocsPage,
  head: () => ({ meta: [{ title: "API Docs - Apex Stats" }] }),
});

const endpoints = [
  ["GET", "/api/local-auth/health", "Проверка доступности auth-сервера."],
  ["GET", "/api/local-auth/me", "Возвращает текущего пользователя по HttpOnly cookie."],
  ["POST", "/api/local-auth/login", "Вход по email и паролю. Выдаёт JWT в HttpOnly cookie."],
  ["POST", "/api/local-auth/logout", "Удаляет cookie текущей сессии."],
  ["GET", "/api/local-auth/users", "Список пользователей. Требуется роль administrator."],
  ["POST", "/api/local-auth/users", "Создание пользователя. Требуется роль administrator."],
  ["POST", "/api/local-auth/users/role", "Изменение роли и инвалидирование старой сессии."],
  ["POST", "/api/local-auth/users/delete", "Удаление пользователя. Нельзя удалить самого себя."],
  ["GET", "/api/local-auth/invites", "Список invite-ссылок. Требуется роль administrator."],
  ["POST", "/api/local-auth/invites", "Создание invite-ссылки с ролью, сроком и лимитом использований."],
  ["POST", "/api/local-auth/invites/delete", "Отзыв invite-ссылки. Требуется роль administrator."],
  ["POST", "/api/local-auth/invites/lookup", "Публичная проверка invite-токена перед регистрацией."],
  ["POST", "/api/local-auth/invites/accept", "Регистрация пользователя по invite-токену."],
] as const;

function Code({ children }: { children: string }) {
  return <code className="rounded-sm bg-surface-2 px-1.5 py-0.5 text-primary">{children}</code>;
}

function DocsPage() {
  return (
    <main className="min-h-screen bg-background px-4 py-16 text-foreground md:px-10">
      <div className="mx-auto max-w-5xl space-y-8">
        <header className="space-y-3">
          <div className="label-eyebrow">Apex Stats</div>
          <h1 className="text-3xl font-bold tracking-tight">API Docs</h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            Серверная часть опубликованной версии отвечает только за авторизацию, роли пользователей и invite-ссылки.
            Анализ видео, OCR, трекинг и внешние аналитические процессы остаются на локальном ПК и не запускаются на VPS.
          </p>
        </header>

        <section className="hud-panel p-5">
          <h2 className="mb-3 text-lg font-bold">Базовая информация</h2>
          <div className="grid gap-3 text-sm md:grid-cols-2">
            <div className="rounded-sm border border-border bg-surface p-3">
              <div className="label-eyebrow mb-1">Base URL</div>
              <Code>/api/local-auth</Code>
            </div>
            <div className="rounded-sm border border-border bg-surface p-3">
              <div className="label-eyebrow mb-1">Session</div>
              <span className="text-muted-foreground">JWT в HttpOnly cookie </span>
              <Code>apex_local_auth</Code>
            </div>
            <div className="rounded-sm border border-border bg-surface p-3">
              <div className="label-eyebrow mb-1">Roles</div>
              <span className="text-muted-foreground">user, operator, administrator</span>
            </div>
            <div className="rounded-sm border border-border bg-surface p-3">
              <div className="label-eyebrow mb-1">Storage</div>
              <span className="text-muted-foreground">JSON-файл на VPS, без видео и аналитических данных</span>
            </div>
          </div>
        </section>

        <section className="hud-panel overflow-hidden">
          <div className="border-b border-border px-5 py-3">
            <h2 className="text-lg font-bold">Endpoints</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-surface-2 label-eyebrow">
                <tr>
                  <th className="px-4 py-3">Method</th>
                  <th className="px-4 py-3">Path</th>
                  <th className="px-4 py-3">Назначение</th>
                </tr>
              </thead>
              <tbody>
                {endpoints.map(([method, path, description]) => (
                  <tr key={`${method}-${path}`} className="border-t border-border">
                    <td className="px-4 py-3 text-mono font-bold text-primary">{method}</td>
                    <td className="px-4 py-3">
                      <Code>{path}</Code>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{description}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid gap-4 md:grid-cols-2">
          <div className="hud-panel p-5">
            <h2 className="mb-3 text-lg font-bold">Пример входа</h2>
            <pre className="overflow-x-auto rounded-sm border border-border bg-surface-2 p-3 text-xs leading-5">
{`POST /api/local-auth/login
Content-Type: application/json

{
  "email": "admin@apex.local",
  "password": "admin12345"
}`}
            </pre>
          </div>

          <div className="hud-panel p-5">
            <h2 className="mb-3 text-lg font-bold">Пример invite</h2>
            <pre className="overflow-x-auto rounded-sm border border-border bg-surface-2 p-3 text-xs leading-5">
{`POST /api/local-auth/invites
Content-Type: application/json

{
  "role": "user",
  "expires_in_days": 7,
  "never_expires": false,
  "max_uses": 1
}`}
            </pre>
          </div>
        </section>

        <section className="hud-panel p-5">
          <h2 className="mb-3 text-lg font-bold">Безопасность</h2>
          <p className="text-sm leading-6 text-muted-foreground">
            Пароли хранятся в виде PBKDF2-хеша с солью. При изменении роли увеличивается версия сессии пользователя,
            поэтому старый JWT перестаёт проходить проверку. Cookie имеет флаги HttpOnly, SameSite=Lax и Secure на VPS.
            Клиент дополнительно проверяет актуальность сессии каждые 15 секунд.
          </p>
        </section>
      </div>
    </main>
  );
}
