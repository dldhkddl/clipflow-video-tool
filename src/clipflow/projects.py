import json
from uuid import uuid4

from .db import Database
from .models import Project, ProjectCreate, ReviewState, utc_now


class ProjectNotFound(LookupError):
    pass


class ProjectRepository:
    def __init__(self, db: Database):
        self.db = db

    def create(self, data: ProjectCreate) -> Project:
        project_id = str(uuid4())
        now = utc_now()
        with self.db.connection() as conn:
            conn.execute(
                """INSERT INTO projects (
                    id, name, affiliate_url, product_name, category,
                    features_json, price_reference, target_seconds,
                    platforms_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    data.name.strip(),
                    str(data.affiliate_url),
                    data.product_name.strip(),
                    data.category.strip(),
                    json.dumps(data.features, ensure_ascii=False),
                    data.price_reference,
                    data.target_seconds,
                    json.dumps(sorted(data.platforms)),
                    now,
                    now,
                ),
            )
        return self.get(project_id)

    def get(self, project_id: str) -> Project:
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return Project(
            id=row["id"],
            name=row["name"],
            affiliate_url=row["affiliate_url"],
            product_name=row["product_name"],
            category=row["category"],
            features=json.loads(row["features_json"]),
            price_reference=row["price_reference"],
            target_seconds=row["target_seconds"],
            platforms=set(json.loads(row["platforms_json"])),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list(self) -> list[Project]:
        with self.db.connection() as conn:
            ids = [row[0] for row in conn.execute("SELECT id FROM projects ORDER BY created_at DESC")]
        return [self.get(project_id) for project_id in ids]

    def delete(self, project_id: str) -> None:
        with self.db.connection() as conn:
            result = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if result.rowcount == 0:
                raise ProjectNotFound(project_id)

    def update_review(self, project_id: str, review: ReviewState) -> ReviewState:
        reviewed_at = utc_now() if review.is_complete else None
        with self.db.connection() as conn:
            result = conn.execute(
                """UPDATE projects SET
                    review_product_facts = ?, review_asset_rights = ?,
                    review_honest_experience = ?, review_affiliate_disclosure = ?,
                    reviewed_at = ?, updated_at = ?
                WHERE id = ?""",
                (
                    int(review.product_facts),
                    int(review.asset_rights),
                    int(review.honest_experience),
                    int(review.affiliate_disclosure),
                    reviewed_at,
                    utc_now(),
                    project_id,
                ),
            )
            if result.rowcount == 0:
                raise ProjectNotFound(project_id)
        return self.get_review(project_id)

    def get_review(self, project_id: str) -> ReviewState:
        with self.db.connection() as conn:
            row = conn.execute(
                """SELECT review_product_facts, review_asset_rights,
                    review_honest_experience, review_affiliate_disclosure, reviewed_at
                FROM projects WHERE id = ?""",
                (project_id,),
            ).fetchone()
        if row is None:
            raise ProjectNotFound(project_id)
        return ReviewState(
            product_facts=bool(row[0]),
            asset_rights=bool(row[1]),
            honest_experience=bool(row[2]),
            affiliate_disclosure=bool(row[3]),
            reviewed_at=row[4],
        )
