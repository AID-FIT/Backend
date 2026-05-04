from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
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
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str] = mapped_column(String(80))

    preferences: Mapped["UserPreference | None"] = relationship(back_populates="user")


class UserPreference(Base, TimestampMixin):
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    styles: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    preferred_colors: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    avoid_items: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    sizes: Mapped[dict] = mapped_column(JSONB, default=dict)

    user: Mapped[User] = relationship(back_populates="preferences")


class ImageAsset(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    storage_url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(120))
    purpose: Mapped[str] = mapped_column(String(40), default="recommendation")


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


class FeedbackEvent(Base, TimestampMixin):
    __tablename__ = "feedback_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    recommendation_id: Mapped[str | None] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    product_id: Mapped[str | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

