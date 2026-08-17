"""ORM models for the knowledge database.

Kept intentionally small but expressive: datasets and fields are stored per (id, region,
delay) so the same concept seen in different regions accumulates as separate rows the
similarity layer can link. JSON columns hold flexible payloads (selected ids, hypotheses)
without a table per shape.
"""

from __future__ import annotations

import time

from sqlalchemy import Boolean, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _now() -> float:
    return time.time()


class Dataset(Base):
    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("dataset_id", "region", "delay", name="uq_dataset_region_delay"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    instrument: Mapped[str] = mapped_column(String, default="EQUITY")
    universe: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="", index=True)
    name: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    value_score: Mapped[float] = mapped_column(Float, default=0.0)
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    fetch_count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class Field(Base):
    __tablename__ = "fields"
    __table_args__ = (UniqueConstraint("field_id", "dataset_id", "region", "delay", name="uq_field_dataset_region_delay"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[str] = mapped_column(String, index=True)
    dataset_id: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, index=True)
    delay: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String, default="MATRIX")
    prefix: Mapped[str] = mapped_column(String, default="", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    alpha_count: Mapped[int] = mapped_column(Integer, default=0)
    is_virgin: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[float] = mapped_column(Float, default=_now)
    last_seen: Mapped[float] = mapped_column(Float, default=_now)


class Usage(Base):
    """Per-user usage counters (operators / fields / categories / datasets / regions), the
    substrate for the diversity engine. Populated as generation and simulation land."""
    __tablename__ = "usage"
    __table_args__ = (UniqueConstraint("kind", "key", "region", name="uq_usage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)     # operator|field|category|dataset|region
    key: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, default="", index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    sum_abs_fitness: Mapped[float] = mapped_column(Float, default=0.0)
    last_at: Mapped[float] = mapped_column(Float, default=_now)


class ResearchSession(Base):
    __tablename__ = "research_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    category: Mapped[str] = mapped_column(String, default="")
    region: Mapped[str] = mapped_column(String, default="")
    delay: Mapped[int] = mapped_column(Integer, default=1)
    goal: Mapped[str] = mapped_column(Text, default="")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    provider: Mapped[str] = mapped_column(String, default="")
    model: Mapped[str] = mapped_column(String, default="")
    paper_name: Mapped[str] = mapped_column(String, default="")
    hypotheses_json: Mapped[str] = mapped_column(Text, default="[]")
    expressions_json: Mapped[str] = mapped_column(Text, default="[]")
    templates_json: Mapped[str] = mapped_column(Text, default="[]")


class SimResult(Base):
    """A simulated alpha and its verdict against the success gate. `passed_gate` and
    `gate_reasons` capture exactly which metrics passed/failed, so the success-rate engine
    and the donation trigger can reason over real, per-metric outcomes."""
    __tablename__ = "sim_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    alpha_id: Mapped[str] = mapped_column(String, default="", index=True)
    expression: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    delay: Mapped[int] = mapped_column(Integer, default=1)
    universe: Mapped[str] = mapped_column(String, default="")
    neutralization: Mapped[str] = mapped_column(String, default="")
    sharpe: Mapped[float] = mapped_column(Float, nullable=True)
    fitness: Mapped[float] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float, nullable=True)
    returns: Mapped[float] = mapped_column(Float, nullable=True)
    margin: Mapped[float] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float] = mapped_column(Float, nullable=True)
    self_corr: Mapped[float] = mapped_column(Float, nullable=True)
    prod_corr: Mapped[float] = mapped_column(Float, nullable=True)
    powerpool_corr: Mapped[float] = mapped_column(Float, nullable=True)
    tests_failed: Mapped[int] = mapped_column(Integer, default=0)
    passed_gate: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    gate_reasons: Mapped[str] = mapped_column(Text, default="[]")
    n_ops: Mapped[int] = mapped_column(Integer, default=0)
    tagged: Mapped[str] = mapped_column(String, default="")
    execution_key: Mapped[str] = mapped_column(String, default="", index=True)
    variant_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    execution_config_json: Mapped[str] = mapped_column(Text, default="{}")


class OperatorRef(Base):
    """The Operator Lab's single source of truth for how each operator is used correctly:
    signature, parameter kinds (positional vs keyword), a canonical example the LLM copies,
    and notes. Seeded from the account's operator definitions + curated examples; the user can
    edit any row (user_edited=True protects it from re-seeding)."""
    __tablename__ = "operator_ref"

    # Composite key: the same operator name (add, multiply, …) exists under REGULAR/SELECTION/COMBO.
    name: Mapped[str] = mapped_column(String, primary_key=True)
    scope: Mapped[str] = mapped_column(String, primary_key=True, default="REGULAR")   # REGULAR|SELECTION|COMBO
    signature: Mapped[str] = mapped_column(String, default="")
    params_json: Mapped[str] = mapped_column(Text, default="[]")   # [{name, kind, required, default}]
    example: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    user_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class Prompt(Base):
    __tablename__ = "prompts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    scope: Mapped[str] = mapped_column(String, default="generate")   # generate|template
    category: Mapped[str] = mapped_column(String, default="")
    region: Mapped[str] = mapped_column(String, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    datasets_json: Mapped[str] = mapped_column(Text, default="[]")
    source_research_id: Mapped[int] = mapped_column(Integer, default=0)


class ResearchDocument(Base):
    __tablename__ = "research_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    title: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String, default="", index=True)
    status: Mapped[str] = mapped_column(String, default="ingested", index=True)
    extraction_json: Mapped[str] = mapped_column(Text, default="{}")


class Experiment(Base):
    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="planned", index=True)
    region: Mapped[str] = mapped_column(String, default="")
    delay: Mapped[int] = mapped_column(Integer, default=1)
    universe: Mapped[str] = mapped_column(String, default="")
    research_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    hypothesis_json: Mapped[str] = mapped_column(Text, default="{}")
    field_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    expression_json: Mapped[str] = mapped_column(Text, default="[]")
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    notes: Mapped[str] = mapped_column(Text, default="")


class AlphaDNA(Base):
    __tablename__ = "alpha_dna"
    __table_args__ = (UniqueConstraint("expression_key", name="uq_alpha_dna_expression"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    expression_key: Mapped[str] = mapped_column(String, index=True)
    expression: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    fields_json: Mapped[str] = mapped_column(Text, default="[]")
    operators_json: Mapped[str] = mapped_column(Text, default="[]")
    categories_json: Mapped[str] = mapped_column(Text, default="[]")
    structure_json: Mapped[str] = mapped_column(Text, default="{}")
    novelty: Mapped[float] = mapped_column(Float, default=0.0)
    robustness: Mapped[float] = mapped_column(Float, default=0.0)
    best_fitness: Mapped[float] = mapped_column(Float, default=0.0)
    best_sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    pass_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)


class FieldInsight(Base):
    __tablename__ = "field_insights"
    __table_args__ = (UniqueConstraint("field_id", "region", name="uq_field_insight_region"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field_id: Mapped[str] = mapped_column(String, index=True)
    region: Mapped[str] = mapped_column(String, default="", index=True)
    category: Mapped[str] = mapped_column(String, default="", index=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    valid_uses: Mapped[int] = mapped_column(Integer, default=0)
    passed_uses: Mapped[int] = mapped_column(Integer, default=0)
    failed_uses: Mapped[int] = mapped_column(Integer, default=0)
    sum_sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    sum_fitness: Mapped[float] = mapped_column(Float, default=0.0)
    successful_operators_json: Mapped[str] = mapped_column(Text, default="[]")
    common_partners_json: Mapped[str] = mapped_column(Text, default="[]")
    last_at: Mapped[float] = mapped_column(Float, default=_now)


class ResearchFailure(Base):
    __tablename__ = "research_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    expression: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    reason: Mapped[str] = mapped_column(String, default="", index=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    experiment_id: Mapped[int] = mapped_column(Integer, default=0, index=True)


class KnowledgeItem(Base):
    """Persistent user/research memory. This is retrieval memory, not model training."""
    __tablename__ = "knowledge_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    item_type: Mapped[str] = mapped_column(String, default="tip", index=True)
    title: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    dataset: Mapped[str] = mapped_column(String, default="", index=True)
    field: Mapped[str] = mapped_column(String, default="", index=True)
    operator: Mapped[str] = mapped_column(String, default="", index=True)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence: Mapped[str] = mapped_column(String, default="unverified")
    source: Mapped[str] = mapped_column(String, default="user")
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="active", index=True)


class LLMBudgetReservation(Base):
    __tablename__ = "llm_budget_reservations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    task: Mapped[str] = mapped_column(String, default="", index=True)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="active", index=True)  # active|released|consumed


class LLMUsage(Base):
    __tablename__ = "llm_usage"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    task: Mapped[str] = mapped_column(String, default="", index=True)
    provider: Mapped[str] = mapped_column(String, default="", index=True)
    model: Mapped[str] = mapped_column(String, default="", index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)


class SubmissionSettings(Base):
    """Singleton configuration for the local submission manager.

    The quota is deliberately configurable because BRAIN submission limits can vary by
    programme/account. ACE uses this as a local guard and never treats it as proof of the
    platform's current server-side allowance.
    """
    __tablename__ = "submission_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    daily_limit: Mapped[int] = mapped_column(Integer, default=4)
    timezone: Mapped[str] = mapped_column(String, default="Africa/Lagos")
    updated_at: Mapped[float] = mapped_column(Float, default=_now)


class SubmissionRecord(Base):
    """One alpha's place in the submission queue/history."""
    __tablename__ = "submission_records"
    __table_args__ = ()

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)
    alpha_id: Mapped[str] = mapped_column(String, index=True)
    sim_result_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    variant_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    execution_key: Mapped[str] = mapped_column(String, default="", index=True)
    expression: Mapped[str] = mapped_column(Text, default="")
    region: Mapped[str] = mapped_column(String, default="", index=True)
    delay: Mapped[int] = mapped_column(Integer, default=1)
    universe: Mapped[str] = mapped_column(String, default="")
    neutralization: Mapped[str] = mapped_column(String, default="")
    sharpe: Mapped[float] = mapped_column(Float, nullable=True)
    fitness: Mapped[float] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float, nullable=True)
    novelty: Mapped[float] = mapped_column(Float, nullable=True)
    robustness: Mapped[float] = mapped_column(Float, nullable=True)
    prod_corr: Mapped[float] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    queued_for: Mapped[str] = mapped_column(String, default="", index=True)  # YYYY-MM-DD
    submitted_at: Mapped[float] = mapped_column(Float, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

class AlphaFamily(Base):
    __tablename__ = "alpha_families"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    updated_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="open", index=True)  # open|closed
    region: Mapped[str] = mapped_column(String, default="", index=True)
    parent_alpha_id: Mapped[str] = mapped_column(String, default="", index=True)
    hypothesis_json: Mapped[str] = mapped_column(Text, default="{}")
    generation: Mapped[int] = mapped_column(Integer, default=0)
    variant_budget: Mapped[int] = mapped_column(Integer, default=30)
    closed_reason: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class AlphaVariant(Base):
    __tablename__ = "alpha_variants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
    family_id: Mapped[int] = mapped_column(Integer, index=True)
    parent_variant_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    parent_alpha_id: Mapped[str] = mapped_column(String, default="", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, index=True)
    mutation_type: Mapped[str] = mapped_column(String, default="", index=True)  # parent|expression|parameter|settings|repair
    label: Mapped[str] = mapped_column(String, default="")
    expression: Mapped[str] = mapped_column(Text, default="")
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="proposed", index=True)  # proposed|running|passed|failed|abandoned|submitted
    alpha_id: Mapped[str] = mapped_column(String, default="", index=True)
    sim_result_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    fitness: Mapped[float] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float, nullable=True)
    novelty: Mapped[float] = mapped_column(Float, nullable=True)
    closed_reason: Mapped[str] = mapped_column(Text, default="")
    execution_key: Mapped[str] = mapped_column(String, default="", index=True)
