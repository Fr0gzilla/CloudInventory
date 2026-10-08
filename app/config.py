import os

DEFAULT_DATABASE_URL = "sqlite:///cloudinventory.db"

# Valeurs d'exemple refusées (cf. CAHIER_DES_CHARGES.md : refus si absent ou valeur d'exemple)
REJECTED_VALUES = {
    "",
    "change-me",
    "changeme",
    "dev-only-insecure-key",
    "replace-me",
}


class Config:
    """Configuration centralisée, lue dans l'environnement (.env chargé par create_app)."""

    SECRET_KEY = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REQUIRED_SECRETS = ("SECRET_KEY", "JWT_SECRET_KEY", "ADMIN_PASSWORD")

    @classmethod
    def validate(cls) -> None:
        """Lève RuntimeError si un secret obligatoire est absent, vide ou une valeur d'exemple."""
        problems = []
        for name in cls.REQUIRED_SECRETS:
            value = os.getenv(name)
            if value is None or value.strip().lower() in REJECTED_VALUES:
                problems.append(name)
                continue
            setattr(cls, name, value.strip())
        if problems:
            raise RuntimeError(
                "Configuration invalide : variables d'environnement manquantes ou "
                "valuées par défaut — " + ", ".join(problems) +
                ". Renseignez-les (cf. .env.example) avant de démarrer."
            )
        cls.SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
        for name, value in cls.export_config().items():
            setattr(cls, name, value)
        from app.notifications import register_notifications

        register_notifications()

    @classmethod
    def use_mock_virt(cls) -> bool:
        """Bascule source simulée/réelle de la virtualisation (USE_MOCK_VIRT, défaut true)."""
        return os.getenv("USE_MOCK_VIRT", "true").lower() == "true"

    @classmethod
    def use_mock_ipam(cls) -> bool:
        """Bascule source simulée/réelle de l'IPAM (USE_MOCK_IPAM, défaut true)."""
        return os.getenv("USE_MOCK_IPAM", "true").lower() == "true"

    @classmethod
    def export_config(cls) -> dict:
        """Lit les paramètres d'export à chaque création d'application."""
        return {
            "EXPORT_ENABLED": os.getenv("EXPORT_ENABLED", "false").lower() == "true",
            "EXPORT_LOCAL_PATH": os.getenv("EXPORT_LOCAL_PATH", "exports"),
            "EXPORT_SMB_PATH": os.getenv("EXPORT_SMB_PATH", ""),
            "EXPORT_SMB_USERNAME": os.getenv("EXPORT_SMB_USERNAME", ""),
            "EXPORT_SMB_PASSWORD": os.getenv("EXPORT_SMB_PASSWORD", ""),
            "EXPORT_RETENTION_CONSOLIDATED": os.getenv("EXPORT_RETENTION_CONSOLIDATED", "30"),
            "EXPORT_RETENTION_RAW": os.getenv("EXPORT_RETENTION_RAW", "7"),
            "EXPORT_RAW_ENABLED": os.getenv("EXPORT_RAW_ENABLED", "false").lower() == "true",
        }
