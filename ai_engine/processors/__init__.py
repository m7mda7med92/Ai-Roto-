"""Job processors. Each processor implements one ``job_type``.

Phase 01 ships only :class:`SimulatedProcessor`. Real processors (segmentation,
tracking, depth, ...) are added in later phases by subclassing
:class:`~ai_engine.processors.base.BaseProcessor` and registering it in
:func:`ai_engine.core.task_manager.build_default_task_manager`.
"""
