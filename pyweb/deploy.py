"""Deploy artifacts for PyWeb (Track B): Dockerfile + k8s manifests only."""

from __future__ import annotations

import os

DOCKERFILE = """\
FROM python:3.12-slim
WORKDIR /srv/app
COPY . /srv/app
RUN pip install --no-cache-dir . 2>/dev/null || pip install --no-cache-dir pyweb 2>/dev/null || true
EXPOSE 8000
ENV PYWEB_HOST=0.0.0.0 PYWEB_PORT=8000
CMD ["python", "-m", "pyweb.deploy", "serve"]
"""

K8S_DEPLOYMENT = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pyweb
  labels:
    app: pyweb
spec:
  replicas: 2
  selector:
    matchLabels:
      app: pyweb
  template:
    metadata:
      labels:
        app: pyweb
    spec:
      containers:
        - name: pyweb
          image: pyweb:latest
          ports:
            - containerPort: 8000
          readinessProbe:
            httpGet:
              path: /__pyweb/health
              port: 8000
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /__pyweb/health
              port: 8000
            periodSeconds: 30
          env:
            - name: PYWEB_HOST
              value: "0.0.0.0"
            - name: PYWEB_PORT
              value: "8000"
"""

K8S_SERVICE = """\
apiVersion: v1
kind: Service
metadata:
  name: pyweb
  labels:
    app: pyweb
spec:
  selector:
    app: pyweb
  ports:
    - port: 80
      targetPort: 8000
  type: ClusterIP
"""


def write_artifacts(outdir: str = ".") -> dict:
    paths = {}
    for name, content in (("Dockerfile", DOCKERFILE),
                          ("k8s-deployment.yaml", K8S_DEPLOYMENT),
                          ("k8s-service.yaml", K8S_SERVICE)):
        path = os.path.join(outdir, name)
        with open(path, "w") as f:
            f.write(content)
        paths[name] = path
    return paths


def main(argv=None):
    import argparse

    p = argparse.ArgumentParser(prog="pyweb.deploy")
    p.add_argument("cmd", nargs="?", default="artifacts",
                   choices=["artifacts", "serve"])
    p.add_argument("--outdir", default=".")
    p.add_argument("--host", default=os.environ.get("PYWEB_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("PYWEB_PORT", "8000")))
    args = p.parse_args(argv)
    if args.cmd == "artifacts":
        for name, path in write_artifacts(args.outdir).items():
            print(f"wrote {path}")
    else:
        from pyweb.app import PyWeb

        PyWeb().run(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
