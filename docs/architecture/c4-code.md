# C4 Code

Rendered SVG: [c4-code.svg](diagrams/c4-code.svg)  
Baseline ADR: [ADR 0001](../adr/0001-current-architecture-baseline.md)

This code view shows the principal modules and symbols that make the current application work. It
is intentionally focused on the implemented runtime path, not roadmap-only features.

```mermaid
flowchart LR
  subgraph bootstrap["Process bootstrap"]
    entrypoint_main["entrypoint.main()"]
    settings_from_env["Settings.from_env()"]
    configure_logging["configure_logging()"]
    bootstrap_database["bootstrap_database()"]
  end

  subgraph app["App assembly"]
    create_app["main.create_app()"]
    register_routes["main.register_routes()"]
    register_obs["main.register_observability()"]
  end

  subgraph identity["Identity and auth"]
    current_user["auth.current_user()"]
    authenticate_session["auth.authenticate_session()"]
    require_verified["auth.require_verified_user()"]
    require_admin["auth.require_admin()"]
    verify_csrf["auth.verify_csrf()"]
    bootstrap_admin["identity.bootstrap_admin()"]
    issue_verification["identity.issue_verification()"]
    issue_reset["identity.issue_reset()"]
    issue_token["tokens.issue_token()"]
  end

  subgraph bottles["Bottle workflow"]
    save_photo["photos.save_photo()"]
    analyze_bottle["analysis.analyze_bottle()"]
    analyze_name["analysis.analyze_bottle_name()"]
    search_prices["analysis.search_bottle_prices()"]
    normalize_analysis["analysis.normalize_analysis()"]
    verified_product["catalog.verified_product()"]
  end

  subgraph providers["Provider adapters"]
    ollama_request["ollama.request_analysis()"]
    openai_request["openai_provider.request_analysis()"]
    openai_prices["openai_provider.search_prices()"]
  end

  subgraph admin["Admin and config"]
    parse_config_form["admin_config.parse_config_form()"]
    settings_values["admin_config.settings_values()"]
    write_managed_config["admin_config.write_managed_config()"]
  end

  subgraph persistence["Persistence"]
    database_cls["database.Database"]
    create_engine["database.create_database_engine()"]
    bootstrap_migrations["migrations.bootstrap_database()"]
    user_model["models.User"]
    bottle_model["models.Bottle"]
    usage_model["models.ApiUsage"]
  end

  subgraph telemetry["Telemetry"]
    recorder["observability.AIUsageRecorder"]
    observed_email["observability.ObservedEmailSender"]
    log_event["logging_config.log_event()"]
    metrics_response["observability.metrics_response()"]
  end

  sqlite[(SQLite / /data)]
  uploads[(Uploads / /data/uploads)]
  config[(Managed config / /data/.env)]
  logs[(Logs / /data/logs)]
  ollama[Ollama]
  openai[OpenAI web search]
  smtp[SMTP relay]

  entrypoint_main --> settings_from_env
  entrypoint_main --> configure_logging
  entrypoint_main --> bootstrap_database
  entrypoint_main --> create_app

  create_app --> database_cls
  create_app --> recorder
  create_app --> observed_email
  create_app --> register_routes
  create_app --> register_obs

  register_routes --> current_user
  register_routes --> require_verified
  register_routes --> require_admin
  register_routes --> authenticate_session
  register_routes --> verify_csrf
  register_routes --> issue_verification
  register_routes --> issue_reset
  register_routes --> save_photo
  register_routes --> analyze_bottle
  register_routes --> analyze_name
  register_routes --> search_prices
  register_routes --> parse_config_form
  register_routes --> settings_values
  register_routes --> write_managed_config
  register_routes --> verified_product
  register_routes --> metrics_response

  create_app --> bootstrap_admin
  analyze_bottle --> ollama_request
  analyze_bottle --> openai_request
  analyze_name --> ollama_request
  analyze_name --> openai_request
  search_prices --> openai_prices
  openai_request --> normalize_analysis

  bootstrap_admin --> issue_verification
  issue_verification --> issue_token
  issue_reset --> issue_token

  database_cls --> create_engine
  bootstrap_migrations --> create_engine
  bootstrap_migrations --> sqlite
  save_photo --> uploads
  parse_config_form --> config
  write_managed_config --> config
  recorder --> sqlite
  recorder --> log_event
  observed_email --> log_event
  observed_email --> smtp
  configure_logging --> logs
  log_event --> logs

  ollama_request --> ollama
  openai_request --> openai
  openai_prices --> openai
  user_model --> sqlite
  bottle_model --> sqlite
  usage_model --> sqlite
```

## Notes

- `entrypoint.py` is the process bootstrap that prepares logging and migrations before Uvicorn
  starts.
- `main.create_app()` assembles the FastAPI app and binds the supporting services.
- The identity path is session-based, CSRF-protected, and bootstrap-aware.
- Bottle analysis can use either Ollama or OpenAI, while price search is OpenAI-only.
- Admin configuration writes to `/data/.env` and expects a restart to take effect.
- Telemetry uses local SQLite usage records, Prometheus metrics, and JSON log output.

## Cross-links

- [C1 System Context](c1-system-context.md)
- [C2 Containers](c2-containers.md)
- [C3 Components](c3-components.md)
- [Rendered SVG](diagrams/c4-code.svg)
