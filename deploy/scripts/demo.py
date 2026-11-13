import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import textwrap
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "deploy/demo/assets"
PASSWORD = "Stepork-Demo-2026!"
ADMIN_PASSWORD = "Stepork-Admin-2026!"
ADMIN_EMAIL = "admin@demo.example.org"
PEOPLE = [
    {
        "first_name": "Анна",
        "last_name": "Соколова",
        "email": "anna@demo.example.org",
        "avatar": "anna-sokolova.png",
        "university": "Университет ИТМО",
        "course": 4,
        "faculty": "Факультет искусственного интеллекта",
        "department": "Лаборатория обработки естественного языка",
        "specialty": "Информатика",
        "city": "Санкт-Петербург",
        "bio": "Исследую обработку естественного языка и поиск по научным публикациям. "
        "Пишу на Python, обучаю модели в PyTorch и люблю воспроизводимые эксперименты. "
        "Ищу команду для проекта на стыке NLP и открытой науки.",
        "skills": ["Python", "NLP", "PyTorch", "Анализ данных"],
        "interests": ["Искусственный интеллект", "Лингвистика"],
        "experience": [
            {
                "title": "Семантический поиск по университетской библиотеке",
                "description": "Подготовила корпус, baseline и оценку качества поиска.",
                "completed": True,
            }
        ],
    },
    {
        "first_name": "Михаил",
        "last_name": "Орлов",
        "email": "mikhail@demo.example.org",
        "avatar": "mikhail-orlov.png",
        "university": "МГУ имени М. В. Ломоносова",
        "course": 3,
        "faculty": "Факультет вычислительной математики и кибернетики",
        "department": "Кафедра математической статистики",
        "specialty": "Прикладная математика",
        "city": "Москва",
        "bio": "Занимаюсь анализом временных рядов и городских экологических данных. "
        "Работаю с Python, SQL и статистическими моделями. Хочу делать исследования, "
        "в которых понятны и методика, и ограничения результата.",
        "skills": ["Python", "SQL", "Статистика", "Анализ данных"],
        "interests": ["Анализ данных", "Биология"],
        "experience": [
            {
                "title": "Анализ городских показаний качества воздуха",
                "description": "Очистил временные ряды и сравнил сезонные модели.",
                "completed": True,
            }
        ],
    },
]
PROJECTS = [
    {
        "title": "Смысловой поиск по научным статьям",
        "owner": 0,
        "access_type": "public_open",
        "summary": "Помогаем исследователям находить близкие по смыслу публикации, даже если ключевые слова не совпадают.",
        "description": "Собираем прототип поиска по открытым аннотациям научных работ. "
        "Сравним BM25 и эмбеддинги, подготовим небольшой размеченный набор запросов "
        "и опубликуем воспроизводимый benchmark. Ищем аналитика и Python-разработчика. "
        "План работы и критерии оценки приложены в PDF.",
        "research_goal": "Сравнить лексический и семантический поиск по Recall@10 и nDCG@10; "
        "описать ошибки и ограничения на русскоязычных научных текстах.",
        "area": "Искусственный интеллект",
        "interests": ["Искусственный интеллект", "Лингвистика"],
        "skills": ["Python", "NLP", "PyTorch"],
        "roles": ["analyst", "developer"],
        "attachment": "План эксперимента и метрики поиска",
        "filename": "semantic-search-plan.pdf",
        "sections": [
            (
                "Задача",
                "Построить воспроизводимое сравнение поиска по словам и поиска по смыслу для открытых научных аннотаций.",
            ),
            (
                "Методика",
                "Собрать 300 аннотаций и 30 исследовательских запросов. Два участника независимо размечают релевантность. Сравнить BM25, sentence embeddings и смешанное ранжирование.",
            ),
            (
                "Оценка",
                "Измерить Recall@10 и nDCG@10. Разделить запросы по научным направлениям. Вручную разобрать ошибки и зафиксировать ограничения маленькой выборки.",
            ),
            (
                "План команды",
                "Неделя 1: корпус и запросы. Неделя 2: baseline и разметка. Неделя 3: эмбеддинги. Неделя 4: анализ, отчёт и открытый код.",
            ),
        ],
    },
    {
        "title": "Качество воздуха: данные городских датчиков",
        "owner": 1,
        "access_type": "public_invite",
        "summary": "Исследуем сезонность PM2.5 и влияние пропусков в показаниях городских датчиков.",
        "description": "Работаем с открытыми временными рядами качества воздуха. "
        "Проверим корректность измерений, построим простые сезонные модели "
        "и подготовим понятный отчёт о неопределённости. Участие по приглашению: "
        "сначала согласуем исследовательскую задачу и роли.",
        "research_goal": "Оценить влияние пропусков и выбросов на краткосрочный прогноз PM2.5 "
        "и сравнить сезонный baseline с регрессионной моделью.",
        "area": "Анализ данных",
        "interests": ["Анализ данных", "Биология"],
        "skills": ["Python", "SQL", "Статистика"],
        "roles": ["analyst", "researcher"],
        "attachment": "Протокол анализа экологических данных",
        "filename": "air-quality-protocol.pdf",
        "sections": [
            (
                "Исследовательский вопрос",
                "Насколько устойчив краткосрочный прогноз PM2.5 к пропускам измерений? Все используемые наблюдения должны иметь открытое происхождение.",
            ),
            (
                "Подготовка данных",
                "Привести часовые отметки к единому часовому поясу. Исключить дубликаты, отметить физически невозможные значения. Не подменять пропуски нулями.",
            ),
            (
                "Эксперимент",
                "Использовать временное разделение train/test без утечки будущего. Сравнить сезонный naive baseline и линейную регрессию по MAE. Отдельно оценить часы с пропусками.",
            ),
            (
                "Ограничения",
                "Демонстрационный протокол не является заключением о безопасности воздуха. Наблюдательные данные не доказывают причинность.",
            ),
        ],
    },
    {
        "title": "Открытая наука: карта воспроизводимости",
        "owner": 0,
        "access_type": "private_invite",
        "summary": "Закрытая рабочая группа готовит критерии воспроизводимости студенческих исследований.",
        "description": "Небольшая команда составляет чек-лист для научных отчётов: доступность данных, "
        "описание окружения, фиксация гипотез и возможность повторить вычисления. "
        "Черновики обсуждаются внутри команды; итоговую методику опубликуем отдельно.",
        "research_goal": "Подготовить практический чек-лист воспроизводимости и проверить его на пяти открытых учебных исследованиях.",
        "area": "Анализ данных",
        "interests": ["Анализ данных"],
        "skills": ["Статистика", "Анализ данных"],
        "roles": ["researcher", "editor"],
        "attachment": "Чек-лист воспроизводимого исследования",
        "filename": "reproducibility-checklist.pdf",
        "sections": [
            (
                "Происхождение данных",
                "Укажите источник, лицензию и дату получения набора данных. Не включайте персональные данные участников без правового основания.",
            ),
            (
                "Вычислительное окружение",
                "Зафиксируйте версии библиотек, зависимости, случайное зерно и аппаратные ограничения. Добавьте команду повторного запуска эксперимента.",
            ),
            (
                "Оценка результата",
                "Разделяйте данные для обучения и проверки. Указывайте метрики, ограничения и отрицательные результаты. Не выдавайте демонстрационные числа за реальные измерения.",
            ),
            (
                "Публикация",
                "Приложите аннотацию, список авторов и PDF. Проверьте, что ссылки открываются без доступа к личному компьютеру автора.",
            ),
        ],
    },
]


def validate_target(environment):
    config = dotenv_values(environment)
    if (
        config.get("SC_ENVIRONMENT") != "development"
        or config.get("COMPOSE_PROJECT_NAME") != "science"
    ):
        raise SystemExit("Demo seed requires the local science development environment")
    if any(
        config.get(key)
        for key in (
            "RABBITMQ_URL",
            "REDIS_URL",
            "S3_INTERNAL_ENDPOINT",
            *(
                f"{service.upper()}_DATABASE_URL"
                for service in (
                    "identity",
                    "people",
                    "projects",
                    "publications",
                    "media",
                    "activity",
                )
            ),
        )
    ):
        raise SystemExit("Demo seed refuses external infrastructure overrides")
    base = config.get("PUBLIC_URL") or f"http://localhost:{config.get('APP_PORT', '18080')}"
    url = urlsplit(base)
    if (
        url.scheme != "http"
        or url.hostname not in {"localhost", "127.0.0.1"}
        or url.port != int(config.get("APP_PORT", "18080"))
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise SystemExit("Demo seed only targets the declared local development port")
    return config, base.rstrip("/")


def pdf_document(title, sections):
    font_path = next(
        (
            path
            for path in (
                Path("/usr/share/fonts/noto/NotoSans-Regular.ttf"),
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
            )
            if path.exists()
        ),
        None,
    )
    if font_path is None:
        raise SystemExit("Install Noto Sans or DejaVu Sans with Cyrillic support for demo PDFs")
    font = ImageFont.truetype(str(font_path), 27)
    heading = ImageFont.truetype(str(font_path), 35)
    title_font = ImageFont.truetype(str(font_path), 43)
    pages = []
    for start in (0, 2):
        page = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(page)
        draw.rectangle((90, 80, 1150, 88), fill="#16705d")
        draw.text((90, 115), "Stepork Science", font=heading, fill="#16705d")
        y = 205
        for line in textwrap.wrap(title, 38):
            draw.text((90, y), line, font=title_font, fill="#182c32")
            y += 64
        y += 60
        for subtitle, body in sections[start : start + 2]:
            draw.text((90, y), subtitle, font=heading, fill="#182c32")
            y += 64
            for line in textwrap.wrap(body, 66):
                draw.text((90, y), line, font=font, fill="#34454b")
                y += 43
            y += 65
        draw.text(
            (90, 1590),
            "Демонстрационный материал • Вымышленные проекты и участники",
            font=font,
            fill="#66797c",
        )
        draw.text(
            (90, 1640),
            f"2026 · Страница {start // 2 + 1} из 2",
            font=font,
            fill="#66797c",
        )
        pages.append(page)
    output = io.BytesIO()
    pages[0].save(
        output,
        "PDF",
        save_all=True,
        append_images=pages[1:],
        resolution=150,
        title=title,
        author="Stepork Science demo",
    )
    return output.getvalue()


class Demo:
    def __init__(self, client):
        self.client = client
        self.dictionaries = {}

    async def request(self, method, path, body=None, user=None):
        headers = {"Idempotency-Key": uuid4().hex}
        if user:
            headers["Authorization"] = "Bearer " + user["access_token"]
        result = await self.client.request(method, "/api/v1" + path, json=body, headers=headers)
        if result.status_code not in {200, 201, 202, 204}:
            raise RuntimeError(
                f"Demo operation {method} {path}: HTTP {result.status_code} {result.text[:300]}"
            )
        return result.json() if result.content else None

    async def listing(self, path, user=None):
        items, cursor = [], None
        while True:
            separator = "&" if "?" in path else "?"
            page = await self.request(
                "GET",
                path + separator + "limit=100" + (f"&cursor={cursor}" if cursor else ""),
                user=user,
            )
            items.extend(page["items"])
            if not page.get("has_more"):
                return items
            cursor = page["next_cursor"]

    async def login(self, email, password):
        user = await self.request("POST", "/auth/login", {"email": email, "password": password})
        account = await self.request("GET", "/account/me", user=user)
        user["id"] = account["user_id"]
        return user

    async def profile(self, user):
        for _ in range(150):
            profile = await self.request("GET", "/me/profile", user=user)
            if profile.get("id"):
                return profile
            await asyncio.sleep(0.2)
        raise RuntimeError("Profile creation timed out; inspect people-worker and RabbitMQ")

    async def dictionary(self, admin, kind, name, parent=None):
        key = kind, name, parent
        if key not in self.dictionaries:
            entries = await self.listing(f"/dictionaries/{kind}")
            entry = next(
                (
                    item
                    for item in entries
                    if item["name"] == name and item.get("parent_id") == parent
                ),
                None,
            )
            if entry is None:
                entry = await self.request(
                    "POST",
                    f"/admin/dictionaries/{kind}",
                    {"name": name, "parent_id": parent},
                    admin,
                )
            self.dictionaries[key] = entry["id"]
        return self.dictionaries[key]

    async def upload(self, user, purpose, resource_id, data, filename):
        upload = await self.request(
            "POST",
            "/media/uploads",
            {
                "purpose": purpose,
                "resource_id": resource_id,
                "filename": filename,
                "content_type": "image/png" if purpose == "avatar" else "application/pdf",
                "size_bytes": len(data),
                "checksum_sha256": base64.b64encode(hashlib.sha256(data).digest()).decode(),
            },
            user,
        )
        async with httpx.AsyncClient(timeout=60) as storage:
            result = await storage.put(
                upload["upload_url"], content=data, headers=upload["required_headers"]
            )
            result.raise_for_status()
        path = f"/media/uploads/{upload['upload_id']}"
        await self.request("POST", path + "/complete", user=user)
        for _ in range(360):
            state = await self.request("GET", path, user=user)
            if state["status"] == "ready":
                return upload
            if state["status"] in {"rejected", "expired", "cancelled"}:
                raise RuntimeError(
                    f"Demo file failed validation: {state['status']} {state.get('rejection_code')}"
                )
            await asyncio.sleep(0.5)
        raise RuntimeError("Demo file scan timed out; inspect media-worker and ClamAV")

    async def seed(self):
        admin = await self.login(ADMIN_EMAIL, ADMIN_PASSWORD)
        profile = await self.profile(admin)
        if profile["first_name"] == "Администратор":
            await self.request(
                "PATCH",
                "/me/profile",
                {
                    "version": profile["version"],
                    "first_name": "Алексей",
                    "last_name": "Волков",
                    "bio": "Администратор демонстрационного стенда Stepork Science.",
                },
                admin,
            )
        users = []
        for person in PEOPLE:
            registration = await self.client.post(
                "/api/v1/auth/register",
                json={
                    "first_name": person["first_name"],
                    "last_name": person["last_name"],
                    "email": person["email"],
                    "password": PASSWORD,
                },
            )
            if registration.status_code != 201 and (
                registration.status_code != 409
                or registration.json().get("error", {}).get("code") != "EMAIL_ALREADY_EXISTS"
            ):
                raise RuntimeError(f"Demo registration: HTTP {registration.status_code}")
            user = await self.login(person["email"], PASSWORD)
            profile = await self.profile(user)
            university = await self.dictionary(admin, "universities", person["university"])
            faculty = await self.dictionary(admin, "faculties", person["faculty"], university)
            department = await self.dictionary(admin, "departments", person["department"], faculty)
            values = {
                "version": profile["version"],
                "first_name": person["first_name"],
                "last_name": person["last_name"],
                "bio": person["bio"],
                "university_id": university,
                "faculty_id": faculty,
                "department_id": department,
                "course": person["course"],
                "specialty_id": await self.dictionary(admin, "specialties", person["specialty"]),
                "city_id": await self.dictionary(admin, "cities", person["city"]),
                "skill_ids": [
                    await self.dictionary(admin, "skills", name) for name in person["skills"]
                ],
                "interest_ids": [
                    await self.dictionary(admin, "research_interests", name)
                    for name in person["interests"]
                ],
                "experience": person["experience"],
                "contacts": [{"type": "email", "value": person["email"], "visibility": "public"}],
            }
            if not profile["avatar_file_id"]:
                upload = await self.upload(
                    user,
                    "avatar",
                    user["id"],
                    (ASSETS / person["avatar"]).read_bytes(),
                    person["avatar"],
                )
                values["avatar_file_id"] = upload["file_id"]
            await self.request("PATCH", "/me/profile", values, user)
            user["university_id"] = university
            users.append(user)
            print(f"Profile ready: {person['first_name']} {person['last_name']}")
        projects = []
        for spec in PROJECTS:
            owner = users[spec["owner"]]
            existing = await self.listing("/me/projects", owner)
            project = next((row for row in existing if row["title"] == spec["title"]), None)
            if project is None:
                project = await self.request(
                    "POST",
                    "/projects",
                    {
                        **{
                            key: spec[key]
                            for key in (
                                "title",
                                "summary",
                                "description",
                                "research_goal",
                                "access_type",
                            )
                        },
                        "area_id": await self.dictionary(admin, "research_areas", spec["area"]),
                        "university_id": owner["university_id"],
                        "team_size": 5,
                        "interest_ids": [
                            await self.dictionary(admin, "research_interests", name)
                            for name in spec["interests"]
                        ],
                        "required_skill_ids": [
                            await self.dictionary(admin, "skills", name) for name in spec["skills"]
                        ],
                        "required_roles": spec["roles"],
                    },
                    owner,
                )
            if project["status"] == "draft":
                project = await self.request(
                    "POST", f"/projects/{project['id']}/publish", user=owner
                )
            attachments = await self.request("GET", f"/projects/{project['id']}/files", user=owner)
            if not any(row["title"] == spec["attachment"] for row in attachments["items"]):
                upload = await self.upload(
                    owner,
                    "project_pdf",
                    project["id"],
                    pdf_document(spec["attachment"], spec["sections"]),
                    spec["filename"],
                )
                await self.request(
                    "POST",
                    f"/projects/{project['id']}/files",
                    {
                        "file_id": upload["file_id"],
                        "reservation_id": upload["reservation_id"],
                        "title": spec["attachment"],
                    },
                    owner,
                )
            projects.append(project)
            print(f"Project ready: {spec['title']}")
        for index in (1, 2):
            project, spec = projects[index], PROJECTS[index]
            owner, guest = users[spec["owner"]], users[1 - spec["owner"]]
            members = await self.listing(f"/projects/{project['id']}/members", owner)
            if not any(member["user_id"] == guest["id"] for member in members):
                invitations = await self.listing(f"/projects/{project['id']}/invitations", owner)
                invitation = next(
                    (
                        item
                        for item in invitations
                        if item["user_id"] == guest["id"] and item["status"] == "pending"
                    ),
                    None,
                )
                if invitation is None:
                    invitation = await self.request(
                        "POST",
                        f"/projects/{project['id']}/invitations",
                        {
                            "user_id": guest["id"],
                            "role": "analyst",
                            "message": "Присоединяйся к исследованию! План работы уже в материалах проекта.",
                        },
                        owner,
                    )
                if index == 2:
                    await self.request(
                        "POST",
                        f"/projects/{project['id']}/invitations/{invitation['id']}/accept",
                        user=guest,
                    )
        papers = []
        for index, title in enumerate(
            (
                "Семантический поиск: протокол воспроизводимого сравнения",
                "Временные ряды качества воздуха: методика подготовки данных",
            )
        ):
            spec, owner, project = PROJECTS[index], users[index], projects[index]
            existing = await self.listing("/me/papers", owner)
            paper = next((row for row in existing if row["title"] == title), None)
            if paper is None:
                paper = await self.request(
                    "POST",
                    "/papers",
                    {
                        "title": title,
                        "abstract": spec["research_goal"]
                        + " Представлены план эксперимента, критерии оценки и ограничения. Демонстрационный методический материал, не отчёт о выполненном исследовании.",
                        "area_id": await self.dictionary(admin, "research_areas", spec["area"]),
                        "university_id": owner["university_id"],
                        "year": 2026,
                        "keywords": ["воспроизводимость", *spec["skills"]],
                    },
                    owner,
                )
            if not paper["file_id"]:
                upload = await self.upload(
                    owner,
                    "paper_pdf",
                    paper["id"],
                    pdf_document(title, spec["sections"]),
                    "research-methodology.pdf",
                )
                paper = await self.request(
                    "PATCH",
                    f"/papers/{paper['id']}",
                    {"version": paper["version"], "file_id": upload["file_id"]},
                    owner,
                )
            if paper["status"] == "draft":
                await self.request("POST", f"/papers/{paper['id']}/publish", user=owner)
            papers.append(paper)
        for target_type, target_id in (
            ("project", projects[0]["id"]),
            ("paper", papers[0]["id"]),
            ("user", users[0]["id"]),
        ):
            await self.request("PUT", f"/favorites/{target_type}/{target_id}", user=users[1])
        return {
            "users": [user["id"] for user in users],
            "projects": [row["id"] for row in projects],
            "papers": [row["id"] for row in papers],
        }


async def main(environment):
    config, base = validate_target(environment)
    prefix = [
        "docker",
        "compose",
        "--env-file",
        str(environment),
        "-p",
        "science",
        "-f",
        str(ROOT / "deploy/compose.yaml"),
        "-f",
        str(ROOT / "deploy/compose.local.yaml"),
    ]
    identifiers = (
        await asyncio.to_thread(
            subprocess.check_output,
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.project=science",
                "--filter",
                "label=com.docker.compose.service=nginx",
                "--format",
                "{{.ID}}",
            ],
            text=True,
        )
    ).split()
    if len(identifiers) != 1:
        raise SystemExit("Start the local development stack before seeding")
    ports = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            [
                "docker",
                "inspect",
                "--format",
                "{{json .NetworkSettings.Ports}}",
                identifiers[0],
            ],
            text=True,
        )
    )
    if not any(
        port["HostIp"] in {"127.0.0.1", "::1"}
        and port["HostPort"] == str(config.get("APP_PORT", "18080"))
        for port in ports.get("80/tcp", [])
    ):
        raise SystemExit("Development gateway must use the declared loopback port")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in config and not key.startswith("COMPOSE_")
    }
    env.update(ADMIN_EMAIL=ADMIN_EMAIL, ADMIN_PASSWORD=ADMIN_PASSWORD)
    await asyncio.to_thread(
        subprocess.run,
        [
            *prefix,
            "exec",
            "-T",
            "-e",
            "ADMIN_EMAIL",
            "-e",
            "ADMIN_PASSWORD",
            "identity",
            "python",
            "-m",
            "libs.platform.cli",
            "create-admin",
        ],
        env=env,
        check=True,
        timeout=60,
    )
    async with httpx.AsyncClient(base_url=base, timeout=40) as client:
        state = await Demo(client).seed()
    print(json.dumps({"url": base, **state}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    asyncio.run(main(parser.parse_args().env_file.resolve()))
