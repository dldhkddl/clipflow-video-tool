from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from enum import StrEnum
from hashlib import sha256
import math
from pathlib import Path
import random
import shutil
import struct
import subprocess
import wave
from uuid import uuid4

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from .db import Database
from .models import ProjectCreate, ReviewState, SceneDraft, ScriptDraft, utc_now
from .workflow import save_script_version


class ReelKind(StrEnum):
    FUN = "fun"
    INFO = "info"
    PROMO = "promo"


class ReelScene(BaseModel):
    prompt: str = Field(min_length=10, max_length=1400)
    caption: str = Field(default="", max_length=80)
    duration_seconds: int = 5


class ReelPlan(BaseModel):
    kind: ReelKind
    title: str
    caption: str
    description: str
    hashtags: list[str] = Field(min_length=8, max_length=12)
    scenes: list[ReelScene] = Field(min_length=3, max_length=3)
    captions_enabled: bool
    duration_seconds: int = 15
    bgm_style: str

    def instagram_text(self) -> str:
        parts = [self.description.strip()]
        if self.kind is ReelKind.PROMO:
            parts.insert(0, "광고 · 제휴 링크 포함")
        parts.append(" ".join(dict.fromkeys(self.hashtags)))
        return "\n\n".join(part for part in parts if part)


class ReelAutomationRecord(BaseModel):
    id: str
    run_date: str
    slot: int
    kind: ReelKind
    status: str
    project_id: str | None = None
    title: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str


class ReelPlanner:
    """짧은 릴스용 안전한 기본 기획을 순환한다.

    유명 곡이나 특정 챌린지를 복제하지 않고, 장르·템포·악기처럼
    일반적인 분위기만 사용한다. 건강 정보는 안전 범위와 금지 식품을
    함께 보여 주고 치료·효능을 단정하지 않는다.
    """

    _KINDS = (ReelKind.FUN, ReelKind.INFO, ReelKind.PROMO)
    _FRUITS = (
        ("사과", "씨와 심지를 완전히 빼고 작게 잘라 주세요"),
        ("수박", "씨와 껍질을 빼고 한입 크기로 소량만 주세요"),
        ("블루베리", "깨끗이 씻어 간식으로 몇 알만 주세요"),
        ("바나나", "껍질을 벗기고 당분을 고려해 조금만 주세요"),
    )

    def __init__(self, site_name: str, dog_name: str):
        self.site_name = site_name
        self.dog_name = dog_name

    def plan(self, index: int) -> ReelPlan:
        kind = self._KINDS[index % len(self._KINDS)]
        fruit, safety = self._FRUITS[(index // len(self._KINDS)) % len(self._FRUITS)]
        if kind is ReelKind.FUN:
            return self._fun(index, fruit)
        if kind is ReelKind.INFO:
            return self._info(fruit, safety)
        return self._promo(fruit)

    def _fun(self, index: int, fruit: str) -> ReelPlan:
        moves = (
            ("두 발을 번갈아 톡톡 딛고 고개를 좌우로 두 번 흔든다", "카메라 쪽으로 세 걸음 다가와 한 바퀴 돈다", "과일 상자 옆에서 앞발을 들고 귀엽게 멈춘다"),
            ("앞발을 짧게 교차하며 리듬을 탄다", "몸을 좌우로 통통 튕기며 꼬리를 흔든다", "카메라를 보고 고개를 갸웃한 뒤 포즈를 잡는다"),
        )[index % 2]
        return ReelPlan(
            kind=ReelKind.FUN,
            title=f"{self.dog_name}의 오늘의 과일 스텝",
            caption=f"오늘도 신선함 체크 완료 🍎 #{self.dog_name} #{self.site_name}",
            description=(
                f"과일가게에 신나는 손님이 찾아왔어요.\n"
                f"{self.dog_name}가 {fruit} 옆에서 오늘만의 오리지널 스텝을 보여 줍니다.\n"
                "어떤 장면이 가장 귀여웠는지 댓글로 알려 주세요."
            ),
            hashtags=self._hashtags(ReelKind.FUN, fruit),
            captions_enabled=False,
            bgm_style="밝은 하이퍼팝 리듬, 124 BPM, 통통 튀는 신스와 손뼉 소리, 완전한 오리지널 멜로디",
            scenes=[
                self._scene(f"실제 반려견 {self.dog_name}가 밝은 과일가게에서 {moves[0]}. {fruit}가 자연스럽게 보인다"),
                self._scene(f"같은 강아지와 같은 장소, {moves[1]}. 빠르고 경쾌한 숏폼 카메라 움직임"),
                self._scene(f"같은 강아지와 같은 장소, {moves[2]}. 표정과 전신이 또렷하고 배경도 선명하다"),
            ],
        )

    def _info(self, fruit: str, safety: str) -> ReelPlan:
        return ReelPlan(
            kind=ReelKind.INFO,
            title=f"강아지에게 {fruit}, 이렇게 주세요",
            caption=f"{fruit} 급여 전 꼭 확인해요. 처음이라면 아주 소량부터, 이상 반응은 수의사와 상담하세요.",
            description=(
                f"강아지에게 {fruit}, 바로 줘도 될까요?\n"
                f"급여 전에는 꼭 기억해 주세요. {safety}.\n"
                "처음 먹는 과일은 아주 작은 양부터 시작하고 간식은 하루 식사의 일부로만 주세요.\n"
                "먹은 뒤 평소와 다른 반응이 보이면 급여를 멈추고 수의사와 상담하세요."
            ),
            hashtags=self._hashtags(ReelKind.INFO, fruit),
            captions_enabled=True,
            bgm_style="따뜻하고 귀여운 로파이 팝, 104 BPM, 우쿨렐레 느낌의 오리지널 멜로디",
            scenes=[
                self._scene(f"{self.dog_name}가 식탁 아래에서 {fruit}를 궁금하게 바라본다", f"{fruit}, 줘도 될까?"),
                self._scene(f"보호자의 손이 {fruit}를 안전하게 손질하고 작게 자른다. 강아지는 기다린다", safety),
                self._scene(f"{self.dog_name}가 보호자의 허락을 기다리고 작은 조각 하나를 받는다", "처음엔 아주 조금만!"),
            ],
        )

    def _promo(self, fruit: str) -> ReelPlan:
        return ReelPlan(
            kind=ReelKind.PROMO,
            title=f"{self.dog_name}의 오늘 과일 검수",
            caption=f"우리 집 과일은 {self.dog_name}가 먼저 구경해요. 오늘의 신선한 {fruit}은 프로필 링크에서 확인하세요.",
            description=(
                f"{self.dog_name}가 오늘 도착한 {fruit}를 먼저 구경했어요.\n"
                "보호자가 직접 상태와 단면을 확인하고 신선한 모습을 영상에 담았습니다.\n"
                f"오늘 준비된 과일은 {self.site_name} 프로필 링크에서 확인할 수 있어요.\n"
                "가격과 판매 상태는 링크의 최신 정보를 확인해 주세요."
            ),
            hashtags=self._hashtags(ReelKind.PROMO, fruit),
            captions_enabled=True,
            bgm_style="산뜻한 미니멀 팝, 112 BPM, 벨과 가벼운 베이스의 완전한 오리지널 멜로디",
            scenes=[
                self._scene(f"{self.dog_name}가 도착한 {fruit} 상자를 반갑게 맞이하고 냄새만 맡는다"),
                self._scene(f"보호자가 윤기 있고 신선한 {fruit}를 들어 단면과 상태를 보여 준다"),
                self._scene(f"{self.dog_name}가 {fruit} 상자 옆에서 카메라를 보고 귀엽게 손을 든다", f"신선한 과일은 {self.site_name}에서"),
            ],
        )

    @staticmethod
    def _scene(action: str, caption: str = "") -> ReelScene:
        guard = (
            " 세로 9:16, 5초, 실사, 선명한 전체 배경, 강아지 외형과 공간의 연속성 유지, "
            "다리와 발의 형태를 자연스럽게 유지, 텍스트와 로고를 영상 안에 생성하지 말 것."
        )
        return ReelScene(prompt=action + guard, caption=caption)

    def _hashtags(self, kind: ReelKind, fruit: str) -> list[str]:
        common = [
            f"#{self.site_name}", f"#{self.dog_name}", "#강아지일상",
            "#반려견", "#강아지릴스", f"#{fruit}",
        ]
        specific = {
            ReelKind.FUN: ["#강아지춤", "#댕댕이", "#귀여운강아지", "#과일가게"],
            ReelKind.INFO: ["#강아지과일", "#반려견건강", "#강아지간식", "#과일급여"],
            ReelKind.PROMO: ["#제철과일", "#신선한과일", "#과일쇼핑", "#과일추천"],
        }[kind]
        return list(dict.fromkeys(common + specific))


class ReelAutomationRepository:
    def __init__(self, database: Database):
        self.database = database

    def reserve_day(self, run_date: date, count: int = 2) -> list[ReelAutomationRecord]:
        if count < 1 or count > 10:
            raise ValueError("하루 생성 수는 1개에서 10개 사이여야 합니다.")
        day = run_date.isoformat()
        now = utc_now()
        with self.database.connection() as conn:
            previous = int(
                conn.execute(
                    "SELECT COUNT(*) FROM reel_automation WHERE run_date < ?",
                    (day,),
                ).fetchone()[0]
            )
            for slot in range(1, count + 1):
                kind = ReelPlanner._KINDS[(previous + slot - 1) % 3].value
                conn.execute(
                    """INSERT OR IGNORE INTO reel_automation (
                        id, run_date, slot, kind, status, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'queued', ?, ?)""",
                    (str(uuid4()), day, slot, kind, now, now),
                )
        return self.list_day(day)

    def list_day(self, run_date: str) -> list[ReelAutomationRecord]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reel_automation WHERE run_date = ? ORDER BY slot",
                (run_date,),
            ).fetchall()
        return [ReelAutomationRecord.model_validate(dict(row)) for row in rows]

    def list_recent(self, limit: int = 20) -> list[ReelAutomationRecord]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM reel_automation ORDER BY run_date DESC, slot DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ReelAutomationRecord.model_validate(dict(row)) for row in rows]

    def update(self, record_id: str, *, status: str, project_id: str | None = None,
               title: str | None = None, error_message: str | None = None) -> ReelAutomationRecord:
        with self.database.connection() as conn:
            conn.execute(
                """UPDATE reel_automation SET status = ?, project_id = COALESCE(?, project_id),
                title = COALESCE(?, title), error_message = ?, updated_at = ? WHERE id = ?""",
                (status, project_id, title, error_message, utc_now(), record_id),
            )
            row = conn.execute(
                "SELECT * FROM reel_automation WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise LookupError(record_id)
        return ReelAutomationRecord.model_validate(dict(row))


class ReelDailyResult(BaseModel):
    run_date: str
    success_count: int
    failure_count: int
    items: list[ReelAutomationRecord]


BuildReel = Callable[[ReelAutomationRecord, ReelPlan], Awaitable[dict]]


class ReelDailyRunner:
    def __init__(
        self,
        repository: ReelAutomationRepository,
        planner: ReelPlanner,
        build_reel: BuildReel,
    ):
        self.repository = repository
        self.planner = planner
        self.build_reel = build_reel

    async def run(self, run_date: date, count: int = 2) -> ReelDailyResult:
        records = self.repository.reserve_day(run_date, count=count)
        completed: list[ReelAutomationRecord] = []
        for record in records:
            if record.status in {"review", "published"}:
                completed.append(record)
                continue
            self.repository.update(record.id, status="generating", error_message=None)
            plan_index = self._plan_index(record)
            plan = self.planner.plan(plan_index)
            error: Exception | None = None
            for attempt in range(2):
                try:
                    result = await self.build_reel(record, plan)
                    completed.append(
                        self.repository.update(
                            record.id,
                            status="review",
                            project_id=result["project_id"],
                            title=result.get("title") or plan.title,
                            error_message=None,
                        )
                    )
                    error = None
                    break
                except Exception as exc:  # 외부 영상 생성기는 한 번만 재시도한다.
                    error = exc
                    if attempt == 0:
                        await asyncio.sleep(0)
            if error is not None:
                completed.append(
                    self.repository.update(
                        record.id,
                        status="failed",
                        title=plan.title,
                        error_message=str(error)[:500],
                    )
                )
        success = sum(item.status in {"review", "published"} for item in completed)
        return ReelDailyResult(
            run_date=run_date.isoformat(),
            success_count=success,
            failure_count=len(completed) - success,
            items=completed,
        )

    def _plan_index(self, record: ReelAutomationRecord) -> int:
        with self.repository.database.connection() as conn:
            earlier = int(
                conn.execute(
                    """SELECT COUNT(*) FROM reel_automation
                    WHERE run_date < ? OR (run_date = ? AND slot < ?)""",
                    (record.run_date, record.run_date, record.slot),
                ).fetchone()[0]
            )
        return earlier


def build_original_bgm(path: Path, *, seed: str, seconds: int = 15) -> Path:
    """외부 음원 없이 짧은 오리지널 팝 루프를 WAV로 합성한다."""
    rate = 24_000
    rng = random.Random(int.from_bytes(sha256(seed.encode("utf-8")).digest()[:8]))
    root = rng.choice((220.0, 246.94, 261.63, 293.66))
    notes = (root, root * 1.25, root * 1.5, root * 2.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        frames = bytearray()
        for index in range(rate * seconds):
            t = index / rate
            beat = int(t * 2) % 8
            note = notes[(beat + (beat // 3)) % len(notes)]
            envelope = max(0.0, 1.0 - ((t * 2) % 1.0) * 1.8)
            melody = math.sin(2 * math.pi * note * t) * 0.22 * envelope
            bass = math.sin(2 * math.pi * (root / 2) * t) * 0.12
            kick_phase = (t * 2) % 1.0
            kick = math.sin(2 * math.pi * (78 - 40 * kick_phase) * t) * max(0, 1 - kick_phase * 7) * 0.32
            clap_phase = (t * 2 + 0.5) % 1.0
            clap = (rng.random() * 2 - 1) * max(0, 1 - clap_phase * 18) * 0.07
            sample = max(-0.92, min(0.92, melody + bass + kick + clap))
            frames.extend(struct.pack("<h", int(sample * 32767)))
        audio.writeframes(frames)
    return path


class ReelMediaBuilder:
    def __init__(self, ffmpeg_path: str | None = None):
        self.ffmpeg_path = ffmpeg_path or imageio_ffmpeg.get_ffmpeg_exe()

    def last_frame(self, clip: Path, output: Path) -> Path:
        output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [self.ffmpeg_path, "-y", "-sseof", "-0.12", "-i", str(clip),
             "-frames:v", "1", str(output)],
            shell=False, check=True, timeout=90, capture_output=True,
        )
        return output

    def build(
        self,
        plan: ReelPlan,
        clips: list[Path],
        output: Path,
        *,
        seed: str,
    ) -> Path:
        if len(clips) != 3:
            raise ValueError("15초 릴스에는 5초 영상 클립 3개가 필요합니다.")
        output.parent.mkdir(parents=True, exist_ok=True)
        work = output.parent / f".reel-{uuid4().hex}"
        work.mkdir(parents=True)
        try:
            segments: list[Path] = []
            for index, (scene, clip) in enumerate(zip(plan.scenes, clips, strict=True)):
                caption = scene.caption if plan.captions_enabled else ""
                disclosure = plan.kind is ReelKind.PROMO and index == 2
                segment = work / f"segment-{index}.mp4"
                self._segment(clip, segment, caption, disclosure=disclosure)
                segments.append(segment)
            concat = work / "concat.txt"
            concat.write_text(
                "\n".join(f"file '{path.as_posix()}'" for path in segments),
                encoding="utf-8",
            )
            bgm = build_original_bgm(work / "오리지널-배경음.wav", seed=seed)
            temporary = work / "완성.mp4"
            subprocess.run(
                [self.ffmpeg_path, "-y", "-f", "concat", "-safe", "0",
                 "-i", str(concat), "-i", str(bgm), "-t", "15",
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "160k", "-shortest",
                 "-movflags", "+faststart", str(temporary)],
                shell=False, check=True, timeout=300, capture_output=True,
            )
            temporary.replace(output)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return output

    def _segment(self, source: Path, output: Path, caption: str, *, disclosure: bool) -> None:
        base_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,fps=30"
        )
        command = [self.ffmpeg_path, "-y", "-stream_loop", "-1", "-i", str(source)]
        if caption or disclosure:
            overlay = output.with_suffix(".png")
            self._overlay(overlay, caption, disclosure=disclosure)
            command.extend(["-loop", "1", "-i", str(overlay), "-t", "5",
                            "-filter_complex",
                            f"[0:v]{base_filter}[base];[base][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
                            "-map", "[v]"])
        else:
            command.extend(["-t", "5", "-vf", f"{base_filter},format=yuv420p"])
        command.extend(["-an", "-c:v", "libx264", "-crf", "20", "-preset", "medium", str(output)])
        subprocess.run(command, shell=False, check=True, timeout=240, capture_output=True)

    @staticmethod
    def _font(size: int) -> ImageFont.ImageFont:
        for candidate in (
            Path("C:/Windows/Fonts/HMFMPYUN.TTF"),
            Path("C:/Windows/Fonts/malgunbd.ttf"),
        ):
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size=size)
        return ImageFont.load_default()

    def _overlay(self, path: Path, caption: str, *, disclosure: bool) -> None:
        image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image, "RGBA")
        if caption:
            font = self._font(76)
            wrapped = self._wrap(draw, caption, font, 860)
            box = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=15, stroke_width=3)
            width = box[2] - box[0] + 90
            height = box[3] - box[1] + 60
            left = (1080 - width) // 2
            top = 1290 - height // 2
            draw.rounded_rectangle((left, top, left + width, top + height), 38,
                                   fill=(255, 248, 235, 235), outline=(255, 126, 107, 255), width=8)
            draw.multiline_text((540, top + height / 2), wrapped, font=font,
                                fill=(65, 42, 35, 255), anchor="mm", align="center",
                                spacing=15, stroke_width=2, stroke_fill=(255, 255, 255, 220))
            draw.ellipse((left - 22, top - 18, left + 25, top + 29), fill=(255, 193, 82, 255))
            draw.ellipse((left + width - 25, top + height - 29, left + width + 22, top + height + 18),
                         fill=(114, 198, 149, 255))
        if disclosure:
            small = self._font(32)
            draw.rounded_rectangle((250, 1768, 830, 1845), 22, fill=(20, 20, 20, 180))
            draw.text((540, 1807), "광고 · 제휴 링크 포함", font=small, fill="white", anchor="mm")
        image.save(path, "PNG")

    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
        lines: list[str] = []
        current = ""
        for char in text.strip():
            candidate = current + char
            if current and draw.textlength(candidate, font=font) > width:
                lines.append(current)
                current = char
            else:
                current = candidate
        if current:
            lines.append(current)
        return "\n".join(lines[:2])


async def build_reel_item(
    services: object,
    record: ReelAutomationRecord,
    plan: ReelPlan,
    media_builder: ReelMediaBuilder | None = None,
) -> dict:
    media = media_builder or ReelMediaBuilder()
    if record.project_id:
        project = services.projects.get(record.project_id)
    else:
        project = services.projects.create(
            ProjectCreate(
                name=f"자동 릴스 {record.run_date} #{record.slot} {plan.title}"[:80],
                affiliate_url=services.settings.brand_site_url,
                product_name=services.settings.brand_name,
                category="강아지와 과일 생활",
                features=["실제 반려견 줄라이", "제철 과일 정보", "사이트 홍보"],
                target_seconds=15,
                platforms={"instagram"},
            )
        )
        record.project_id = project.id
        services.reel_automation.update(record.id, status="generating", project_id=project.id, title=plan.title)

    profile = services.video_profile.load()
    reference = services.video_profile.reference_path
    start_image = reference if reference.is_file() and not reference.is_symlink() else None
    clips: list[Path] = []
    asset_ids: list[str] = []
    project_dir = services.settings.data_dir / "projects" / project.id
    project_dir.mkdir(parents=True, exist_ok=True)
    for index, scene in enumerate(plan.scenes, start=1):
        prompt = profile.compose(scene.prompt) if profile.enabled else scene.prompt
        created = services.ai_assets.generate(
            project, "video", prompt, start_image, confirmed_cost=True
        )
        video_asset = next(asset for asset in reversed(created) if asset.media_type == "video")
        clips.append(video_asset.path)
        asset_ids.append(video_asset.id)
        next_frame = project_dir / f"연속장면-{index}.png"
        start_image = media.last_frame(video_asset.path, next_frame)

    output = project_dir / "video-vertical.mp4"
    media.build(plan, clips, output, seed=f"{record.run_date}-{record.slot}-{plan.kind.value}")
    draft_scenes = []
    for index in range(5):
        source = plan.scenes[min(index // 2, 2)]
        draft_scenes.append(
            SceneDraft(
                order=index + 1,
                narration="배경음 중심 영상",
                on_screen_text=source.caption if plan.captions_enabled else "",
                duration_seconds=3,
                asset_id=asset_ids[min(index // 2, 2)],
                asset_hint=source.prompt[:120],
            )
        )
    save_script_version(
        services,
        project.id,
        ScriptDraft(
            hook=plan.title,
            selection_criteria=["강아지 외형 연속성", "한눈에 이해되는 15초 구성"],
            pros=["오리지널 배경음", "세로형 영상"],
            cons=["게시 전 사실과 화면을 직접 확인해야 함"],
            cta="프로필 링크에서 확인하세요" if plan.kind is ReelKind.PROMO else "",
            scenes=draft_scenes,
            fact_check_notes=["포도와 건포도는 강아지에게 주지 않음", "과일은 소량 간식으로만 제공"],
        ),
    )
    services.projects.update_review(project.id, ReviewState())
    (project_dir / "릴스-기획.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (project_dir / "인스타그램-문구.txt").write_text(
        plan.instagram_text(), encoding="utf-8"
    )
    return {"project_id": project.id, "title": plan.title, "video_path": str(output)}
