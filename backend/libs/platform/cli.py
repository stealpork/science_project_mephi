import argparse
import asyncio
import getpass
import os
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from libs.platform.config import get_settings
from libs.platform.database import Database, append_event, uid


async def run(args):
    settings = get_settings()
    db = Database(settings)
    async with db.sessions() as session, session.begin():
        if args.action == "create-admin":
            if settings.service_name != "identity":
                raise SystemExit("Run this command in identity")
            from services.identity.auth import hash_password
            from services.identity.models import User

            email = os.environ.get("ADMIN_EMAIL") or await asyncio.to_thread(input, "Admin email: ")
            password = os.environ.get("ADMIN_PASSWORD") or await asyncio.to_thread(
                getpass.getpass, "Admin password: "
            )
            assert email is not None and password is not None
            if len(password) < 12:
                raise SystemExit("Use a password of at least 12 characters")
            row = await session.scalar(select(User).where(User.email == email.lower()))
            if row:
                if "admin" not in row.roles:
                    raise SystemExit(
                        "Account already exists without admin role; use an audited role change"
                    )
                print("Administrator already exists; credentials preserved")
                return
            row = User(
                id=uid(),
                email=email.lower(),
                password_hash=await hash_password(password),
                roles=["user", "admin"],
                email_verified=True,
            )
            if settings.admin_2fa_required:
                import pyotp
                from cryptography.fernet import Fernet

                secret = os.environ.get("ADMIN_TOTP_SECRET")
                if not secret:
                    raise SystemExit(
                        "Set ADMIN_TOTP_SECRET from your authenticator/password manager for initial administrator MFA"
                    )
                pyotp.TOTP(secret).now()
                row.mfa_secret = (
                    Fernet(settings.mfa_encryption_key.encode()).encrypt(secret.encode()).decode()
                )
            session.add(row)
            await session.flush()
            await append_event(
                session,
                "identity.user_registered",
                row.id,
                {"user_id": row.id, "first_name": "Администратор", "last_name": "Платформы"},
            )
            print("Administrator created; password was not printed")
        elif args.action == "seed-users":
            if settings.service_name != "identity" or settings.environment == "production":
                raise SystemExit("Demo seed is allowed only in non-production identity")
            from services.identity.auth import hash_password
            from services.identity.models import User

            password = os.environ.get("DEMO_PASSWORD", "Demo-only-science2026!")
            hashed = await hash_password(password)
            for number in range(args.count):
                identifier = str(uuid5(NAMESPACE_URL, f"science:demo-user:{number}"))
                if await session.get(User, identifier):
                    continue
                session.add(
                    User(
                        id=identifier,
                        email=f"student{number:04}@demo.example.org",
                        password_hash=hashed,
                        email_verified=True,
                    )
                )
                await append_event(
                    session,
                    "identity.user_registered",
                    identifier,
                    {
                        "user_id": identifier,
                        "first_name": "Исследователь",
                        "last_name": f"Демонстрационный {number:04}",
                        "course": number % 5 + 1,
                    },
                )
            print(
                f"Demo users ensured: {args.count}. Shared DEMO_PASSWORD is for local load tests only"
            )
        elif args.action == "seed-projects":
            if settings.service_name != "projects" or settings.environment == "production":
                raise SystemExit("Demo seed is allowed only in non-production projects")
            from services.projects.models import Member, Project

            area = str(uuid5(NAMESPACE_URL, "science:research_areas:Искусственный интеллект"))
            skill = str(uuid5(NAMESPACE_URL, "science:skills:Python"))
            for number in range(args.count):
                identifier = str(uuid5(NAMESPACE_URL, f"science:demo-project:{number}"))
                if await session.get(Project, identifier):
                    continue
                owner = str(uuid5(NAMESPACE_URL, f"science:demo-user:{number % 1000}"))
                session.add(
                    Project(
                        id=identifier,
                        creator_id=owner,
                        creator_name=f"Исследователь Демонстрационный {number % 1000:04}",
                        owner_id=owner,
                        title=f"Научное исследование {number:04}: анализ данных",
                        summary="Демонстрационный проект для исследования и нагрузочного тестирования",
                        description="Исследуем применение открытых технологий к научным задачам.",
                        research_goal="Подготовить воспроизводимое исследование",
                        area_id=area,
                        required_skill_ids=[skill],
                        team_size=5,
                        status="recruiting",
                    )
                )
                await session.flush()
                session.add(Member(project_id=identifier, user_id=owner, role="leader"))
            print(f"Demo projects ensured: {args.count}")
        elif args.action == "seed-dictionaries":
            if settings.service_name != "people":
                raise SystemExit("Run this command in people")
            from services.people.models import DictionaryEntry

            groups = {
                "universities": ["МГУ имени М. В. Ломоносова", "Университет ИТМО", "НИУ ВШЭ"],
                "skills": ["Python", "NLP", "PyTorch", "R", "SQL", "Статистика", "Анализ данных"],
                "research_areas": [
                    "Искусственный интеллект",
                    "Анализ данных",
                    "Биология",
                    "Гуманитарные науки",
                ],
                "research_interests": [
                    "Искусственный интеллект",
                    "Анализ данных",
                    "Биология",
                    "Лингвистика",
                ],
                "specialties": ["Прикладная математика", "Информатика", "Биология"],
                "cities": ["Москва", "Санкт-Петербург"],
                "team_roles": [
                    "leader",
                    "researcher",
                    "analyst",
                    "developer",
                    "designer",
                    "author",
                    "editor",
                ],
            }
            for kind, names in groups.items():
                for name in names:
                    identifier = str(uuid5(NAMESPACE_URL, f"science:{kind}:{name}"))
                    if not await session.get(DictionaryEntry, identifier):
                        session.add(
                            DictionaryEntry(
                                id=identifier,
                                kind=kind,
                                name=name,
                                normalized_name=name.casefold(),
                                parent_key="",
                                skill_kind="technology"
                                if kind == "skills" and name in {"Python", "R", "SQL", "PyTorch"}
                                else None,
                            )
                        )
            print("Initial dictionaries configured with stable IDs")
    await db.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["create-admin", "seed-dictionaries", "seed-users", "seed-projects"]
    )
    parser.add_argument("--count", type=int, default=1000)
    asyncio.run(run(parser.parse_args()))
