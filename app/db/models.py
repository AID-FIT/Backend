from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    nickname: Mapped[str] = mapped_column(String(80))
    role: Mapped[str] = mapped_column(String(30), default="guest", server_default="guest", index=True)

    preferences: Mapped["UserPreference | None"] = relationship(back_populates="user")
    social_identities: Mapped[list["SocialIdentity"]] = relationship(back_populates="user")


class SocialIdentity(Base, TimestampMixin):
    __tablename__ = "social_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_sub", name="uq_social_provider_sub"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    provider_sub: Mapped[str] = mapped_column(String(255), index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    raw_claims: Mapped[dict] = mapped_column(JSONB, default=dict)

    user: Mapped[User] = relationship(back_populates="social_identities")


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"
    __table_args__ = (
        # 성별은 카탈로그 필터로 그대로 쓰인다. 표기가 흔들리면 조건이 빗나가므로
        # 정규형만 들어오도록 DB에서 막는다. 정규화는 schemas/user.py가 한다.
        CheckConstraint(
            "gender IS NULL OR gender IN ('men', 'women', 'unisex')",
            name="ck_user_preferences_gender",
        ),
        CheckConstraint(
            "height_cm IS NULL OR (height_cm BETWEEN 100 AND 250)",
            name="ck_user_preferences_height_cm",
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    styles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    preferred_colors: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    avoid_items: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    sizes: Mapped[dict] = mapped_column(JSONB, default=dict)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    height_cm: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    user: Mapped[User] = relationship(back_populates="preferences")


class ImageAsset(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    storage_url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(40), default="recommendation")


class ClosetItem(Base, TimestampMixin):
    __tablename__ = "closet_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    image_id: Mapped[str] = mapped_column(ForeignKey("images.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    brand: Mapped[str] = mapped_column(String(120))
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    sub_category: Mapped[str | None] = mapped_column(String(120), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(30), nullable=True)
    image_url: Mapped[str] = mapped_column(Text)
    product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(80), nullable=True)
    material: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fit: Mapped[str | None] = mapped_column(String(80), nullable=True)
    pattern: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mood: Mapped[str | None] = mapped_column(String(80), nullable=True)
    sense_of_season: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_match: Mapped[bool] = mapped_column(default=True)
    raw_vlm_result: Mapped[dict] = mapped_column(JSONB, default=dict)


class Product(Base, TimestampMixin):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    brand: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(80), index=True)
    price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class ProductEmbedding(Base, TimestampMixin):
    __tablename__ = "product_embeddings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), index=True)
    source_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[dict] = mapped_column(JSONB, default=dict)


class RecommendationRequest(Base, TimestampMixin):
    __tablename__ = "recommendation_requests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    image_id: Mapped[str | None] = mapped_column(ForeignKey("images.id"), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class VlmAnalysis(Base, TimestampMixin):
    __tablename__ = "vlm_analyses"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    request_id: Mapped[str] = mapped_column(ForeignKey("recommendation_requests.id"), index=True)
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    is_clothing: Mapped[bool] = mapped_column(default=True)
    confidence: Mapped[int] = mapped_column(Integer, default=0)


class Recommendation(Base, TimestampMixin):
    __tablename__ = "recommendations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    request_id: Mapped[str] = mapped_column(ForeignKey("recommendation_requests.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    summary: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    raw_agent_output: Mapped[dict] = mapped_column(JSONB, default=dict)


class RecommendationItem(Base, TimestampMixin):
    __tablename__ = "recommendation_items"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    recommendation_id: Mapped[str] = mapped_column(ForeignKey("recommendations.id"), index=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text)
    rank: Mapped[int] = mapped_column(Integer)


class ChatConversation(Base, TimestampMixin):
    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="ck_chat_messages_role"),
        # 대화 내역은 항상 (conversation_id, created_at) 순으로 읽는다.
        Index("ix_chat_messages_conversation_created_at", "conversation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    # content는 채팅 UI 표시와 모델 대화 내역 구성용,
    # payload는 AgentResponse 전체나 이미지 URL을 손실 없이 보관한다.
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")


class FeedbackEvent(Base, TimestampMixin):
    __tablename__ = "feedback_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    recommendation_id: Mapped[str | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
