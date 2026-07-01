from flask import Flask, g
import os
from datetime import timedelta

def create_app():
    """  Create app instance at module level """
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET", "default_secret_key")
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=120)

    # Initialize admin databases
    from . import admin_db, migrate_db
    admin_db.init_db()
    migrate_db.init_db()


    # Register teardown handler to close database connections
    @app.teardown_appcontext
    def close_db(error):
        """Close database connection at the end of each request"""
        db = g.pop("db", None)
        if db is not None:
            db.close()

    # Context processor to inject branding variables into all templates
    @app.context_processor
    def inject_branding():
        """Inject logo and organization info into all templates"""
        from . import mydb_config
        return {
            "logo_path": mydb_config.organizationLogo,
            "org_name": mydb_config.organizationName,
            "supportEmail": mydb_config.supportEmail,
            "supportOrganization": mydb_config.supportOrganization,
            "backup_purge_period": mydb_config.backup_purge_period,
        }


    # Import views to register routes (must be after app configuration)
    from . import mydb_views
    mydb_views.register(app)

    return app
