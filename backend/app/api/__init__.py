from .autocomplete import router as autocomplete_router
from .biomeme_discovery import router as biomeme_discovery_router
from .biomeme_runs import router as biomeme_runs_router
from .databases import router as databases_router
from .discovery import router as discovery_router
from .export import router as export_router
from .lookup_values import router as lookup_values_router
from .nanopore_run_accessions import router as nanopore_run_accessions_router
from .nanopore_runs import router as nanopore_runs_router
from .pipeline import router as pipeline_router
from .samples import router as samples_router
from .settings import router as settings_router
from .sites import router as sites_router
from .sync import router as sync_router

__all__ = [
    "biomeme_discovery_router",
    "sites_router",
    "nanopore_run_accessions_router",
    "nanopore_runs_router",
    "biomeme_runs_router",
    "samples_router",
    "lookup_values_router",
    "settings_router",
    "export_router",
    "autocomplete_router",
    "discovery_router",
    "pipeline_router",
    "databases_router",
    "sync_router",
]
