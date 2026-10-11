"""Routing of the AI apps through the gateway's TLS front end (phase 3b): the name-constrained local CA,
the certificate store, the PAC file and the per-user Internet Settings, behind backends that have a
fake for every test. Nothing in this package touches the real system unless ``guard.real_system_ok()``
says so (rule R8)."""
