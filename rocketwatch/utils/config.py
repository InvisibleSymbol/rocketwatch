import tomllib
from typing import Any, Literal

from pydantic import BaseModel


class DiscordOwner(BaseModel):
    user_id: int
    server_id: int


class DiscordConfig(BaseModel):
    secret: str
    owner: DiscordOwner
    channels: dict[str, int]


class ExecutionLayerEndpoint(BaseModel):
    current: str
    mainnet: str
    archive: str | None = None


class ExecutionLayerConfig(BaseModel):
    explorer: str
    endpoint: ExecutionLayerEndpoint


class ConsensusLayerConfig(BaseModel):
    explorer: str
    endpoint: str
    beaconcha_secret: str


class MongoDBConfig(BaseModel):
    uri: str


class RocketPoolSupport(BaseModel):
    user_ids: list[int]
    role_ids: list[int]
    server_id: int
    channel_id: int
    moderator_id: int


class DmWarningConfig(BaseModel):
    channels: list[int]


class RocketPoolConfig(BaseModel):
    chain: str = "mainnet"
    manual_addresses: dict[str, str]
    dao_multisigs: list[str]
    support: RocketPoolSupport
    dm_warning: DmWarningConfig


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai", "google", ""] = ""
    api_key: str = ""
    model: str = ""


class STTConfig(BaseModel):
    provider: Literal["openai", ""] = ""
    api_key: str = ""
    model: str = "whisper-1"
    base_url: str = ""


class TranscriptionConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    stt: STTConfig = STTConfig()
    voice_channel_id: int = 0
    output_channel_id: int = 0
    min_users: int = 3
    leave_grace_seconds: int = 120
    max_recording_minutes: int = 180
    chunk_duration_seconds: int = 600
    min_transcript_words: int = 100


class SentinelConfig(BaseModel):
    api_url: str = ""
    api_key: str = ""
    timeout_seconds: int = 600


class ModulesConfig(BaseModel):
    include: list[str] = []
    exclude: list[str] = []
    enable_commands: bool | None = None


class StatusMessageFieldConfig(BaseModel):
    name: str
    value: str


class StatusMessageConfig(BaseModel):
    plugin: str
    cooldown: int
    fields: list[StatusMessageFieldConfig] = []


class EventsConfig(BaseModel):
    lookback_distance: int
    genesis: int
    block_batch_size: int
    status_message: dict[str, StatusMessageConfig] = {}


class SecretsConfig(BaseModel):
    cronitor: str = ""


class Config(BaseModel):
    log_level: str = "DEBUG"
    discord: DiscordConfig
    mongodb: MongoDBConfig
    modules: ModulesConfig = ModulesConfig()
    execution_layer: ExecutionLayerConfig
    consensus_layer: ConsensusLayerConfig
    rocketpool: RocketPoolConfig
    events: EventsConfig
    sentinel: SentinelConfig = SentinelConfig()
    llm: LLMConfig = LLMConfig()
    transcription: TranscriptionConfig = TranscriptionConfig()
    secrets: SecretsConfig = SecretsConfig()


class _ConfigProxy:
    _instance: Config | None = None

    def __init__(self, path: str = "config.toml") -> None:
        self.__path = path

    def __load_config(self) -> None:
        with open(self.__path, "rb") as f:
            data = tomllib.load(f)
        cfg._instance = Config(**data)

    def __getattr__(self, name: str) -> Any:
        if self._instance is None:
            self.__load_config()
        return getattr(self._instance, name)


cfg = _ConfigProxy()
