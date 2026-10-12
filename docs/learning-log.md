# Learning log

One entry per phase: what was built, the key concepts, and the questions an interviewer is most likely to ask.

---

## Phase 0: Scaffold

### What was built
- Repo layout: `charts/` (two Helm charts, filled in later), `services/` (five services), `scripts/`, `docs/`.
- `k3d/cluster.yaml`: a 2-node local Kubernetes cluster (1 server + 1 agent) running k3s `v1.36.5+k3s1`, with host ports 80/443 mapped to the cluster's ingress.
- `Makefile`: `cluster-up` (idempotent), `status`, `teardown`, and stubs for later targets.
- `.gitignore` (secrets, PDFs, build output) and `.gitattributes` (forces LF line endings so scripts run in WSL).

### Key concepts
- **k3s**: a lightweight but fully conformant Kubernetes distribution, shipped as a single binary. It bundles Traefik (ingress controller), a local-path storage provisioner, a service load balancer (ServiceLB/klipper), and a NetworkPolicy controller.
- **k3d**: runs k3s *inside Docker containers*. Each "node" is a container. That gives a disposable local cluster that runs the same k3s as the VPS.
- **Nodes, server vs agent**: a node is a machine (here a container) that runs pods. The *server* runs the control plane (API server, scheduler, controller manager, datastore) and can also run pods. *Agents* only run pods.
- **Ingress vs ingress controller**: an Ingress is just a routing rule ("host shop.localtest.me → service wordpress"). The *ingress controller* (Traefik) is the actual reverse proxy that reads those rules and forwards traffic. Without a controller, Ingress objects do nothing.
- **Port mapping**: k3d puts an nginx loadbalancer container in front of the nodes. `80:80@loadbalancer` publishes host port 80 on it, and it forwards to Traefik's LoadBalancer Service inside the cluster.
- **localtest.me**: a public DNS wildcard where every subdomain resolves to 127.0.0.1, so hostname-based Ingress routing works locally without editing hosts files.

### Likely interview questions
1. **Why k3d instead of Kind or Minikube?**
   k3d runs k3s, the same distribution I deploy on the VPS. Traefik, local-path storage and the NetworkPolicy enforcement behave the same in both places, so "works locally" means "works in prod" with only values-file changes. Kind would need an ingress controller and a CNI with NetworkPolicy support added by hand.
2. **When you open http://shop.localtest.me, what happens step by step?**
   DNS resolves it to 127.0.0.1. The browser connects to port 80 on the host, which Docker publishes on the k3d loadbalancer container. That container forwards to Traefik inside the cluster. Traefik matches the Host header against Ingress rules and proxies to the matching Service, which load-balances to a ready pod. Today there's no rule yet, so Traefik returns 404, which proves everything up to Traefik works.
3. **Why pin the k3s version?**
   For reproducibility. Kubernetes minor versions change APIs and defaults. Pinning the image locally and setting `INSTALL_K3S_VERSION` on the VPS to the same version removes "different cluster version" as a source of bugs.
