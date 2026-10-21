{{/*
Pod template for a wp-cli Job: shared by the bootstrap Job (every install/upgrade)
and the on-demand reset-demo Job. Usage:
  include "store.wpcliPod" (dict "ctx" $ "script" "bootstrap.sh" "component" "bootstrap")

Why an initContainer: the wordpress:cli image has wp-cli but no WordPress core,
and the WordPress pod's core files are not on a volume. So we copy core from
the SAME wordpress image (same version as the live site) into an emptyDir, and
mount the real wp-content PVC on top. Plugins wp-cli installs land on the PVC,
where the running site reads them.
The pod carries the label app.kubernetes.io/name=bootstrap, which is what the
store NetworkPolicies allow to reach MariaDB.
*/}}
{{- define "store.wpcliPod" -}}
{{- $ := .ctx -}}
metadata:
  labels: {{- include "store.labels" $ | nindent 4 }}
    app.kubernetes.io/name: bootstrap
    app.kubernetes.io/component: {{ .component }}
spec:
  restartPolicy: Never
  automountServiceAccountToken: false
  securityContext:
    runAsNonRoot: true
    runAsUser: 33           # www-data in both images, so files on the PVC match the site
    runAsGroup: 33
    fsGroup: 33
    seccompProfile: { type: RuntimeDefault }
  initContainers:
    - name: copy-core
      image: "{{ $.Values.images.wordpress.repository }}:{{ $.Values.images.wordpress.tag }}"
      imagePullPolicy: {{ $.Values.images.wordpress.pullPolicy }}
      command:
        - sh
        - -c
        - |
          set -eu
          cp -r /usr/src/wordpress/. /work/
          # Same config the live site uses: reads WORDPRESS_DB_* from env.
          cp /work/wp-config-docker.php /work/wp-config.php
      volumeMounts:
        - name: core
          mountPath: /work
      resources: {{- toYaml $.Values.bootstrap.resources | nindent 8 }}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: { drop: ["ALL"] }
  containers:
    - name: wpcli
      image: "{{ $.Values.images.wpcli.repository }}:{{ $.Values.images.wpcli.tag }}"
      imagePullPolicy: {{ $.Values.images.wpcli.pullPolicy }}
      command: ["sh", "/bootstrap/{{ .script }}"]
      workingDir: /var/www/html
      env:
        - name: WORDPRESS_DB_HOST
          value: {{ $.Release.Name }}-mariadb
        - name: WORDPRESS_DB_NAME
          value: {{ $.Values.mariadb.database | quote }}
        - name: WORDPRESS_DB_USER
          value: {{ $.Values.mariadb.user | quote }}
        - name: WORDPRESS_DB_PASSWORD
          valueFrom: { secretKeyRef: { name: {{ $.Release.Name }}-db, key: password } }
        - name: WP_ADMIN_USER
          valueFrom: { secretKeyRef: { name: {{ $.Release.Name }}-wp-admin, key: username } }
        - name: WP_ADMIN_PASSWORD
          valueFrom: { secretKeyRef: { name: {{ $.Release.Name }}-wp-admin, key: password } }
        - name: WP_ADMIN_EMAIL
          value: {{ $.Values.wordpress.admin.email | quote }}
        - name: SITE_URL
          value: {{ include "store.siteUrl" $ | quote }}
        - name: SITE_TITLE
          value: {{ $.Values.wordpress.siteTitle | quote }}
        - name: STORE_TIMEZONE
          value: {{ $.Values.woocommerce.timezone | quote }}
        - name: STORE_CURRENCY
          value: {{ $.Values.woocommerce.currency | quote }}
        - name: STORE_COUNTRY
          value: {{ $.Values.woocommerce.country | quote }}
        - name: WOO_VERSION
          value: {{ $.Values.woocommerce.version | quote }}
        - name: WOO_API_USER
          value: {{ $.Values.woocommerce.apiUser.login | quote }}
        - name: WOO_API_EMAIL
          value: {{ $.Values.woocommerce.apiUser.email | quote }}
        - name: WOO_CONSUMER_KEY
          valueFrom: { secretKeyRef: { name: {{ $.Release.Name }}-woo-api, key: consumer-key } }
        - name: WOO_CONSUMER_SECRET
          valueFrom: { secretKeyRef: { name: {{ $.Release.Name }}-woo-api, key: consumer-secret } }
        - name: WP_CLI_CACHE_DIR
          value: /tmp/wp-cli-cache
      envFrom:
        - secretRef: { name: {{ $.Release.Name }}-wp-salts }
      volumeMounts:
        - name: core
          mountPath: /var/www/html
        - name: wp-content
          mountPath: /var/www/html/wp-content
        - name: bootstrap
          mountPath: /bootstrap
          readOnly: true
      resources: {{- toYaml $.Values.bootstrap.resources | nindent 8 }}
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: { drop: ["ALL"] }
  volumes:
    - name: core
      emptyDir: {}
    - name: wp-content
      persistentVolumeClaim:
        claimName: {{ $.Release.Name }}-wp-content
    - name: bootstrap
      configMap:
        name: {{ $.Release.Name }}-bootstrap
{{- end }}
