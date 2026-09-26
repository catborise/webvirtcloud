# WebVirtCloud — Kapsamlı Kod İnceleme, Güvenlik Denetimi ve Yol Haritası (Roadmap)

Bu belge; **Antigravity** ve **Codex** tarafından gerçekleştirilen modül bazlı bağımsız güvenlik, güvenilirlik, mimari ve geliştirme denetimlerinin sentezini ve önceliklendirilmiş aksiyon planını içermektedir.

---

## 1. Yönetici Özeti ve Karşılaştırmalı Değerlendirme

WebVirtCloud kod tabanı; temel sanallaştırma orkestrasyonu için işlevsel bir temel sunmakla birlikte, özellikle **REST API yetkilendirme modelleri**, **konsol oturum doğrulama mekanizmaları**, **libvirt eşzamanlılığı (concurrency)** ve **veri bütünlüğü korumaları** açısından önemli güvenlik ve kararlılık riskleri barındırmaktadır.

### İnceleme Perspektifleri Karşılaştırması

| Kapsam | Antigravity Odak Noktaları | Codex Odak Noktaları | Birleşik Konsensus / Kritik Aksiyon |
|---|---|---|---|
| **API & Yetkilendirme** | `ComputeSerializer` düz metin parola sızıntısı; `ComputeViewSet` normal kullanıcılara açık; `instances` nested endpoint bilgi ifşası. | API viewset'lerinde genel RBAC ve tenant izolasyonu yokluğu; `FlavorViewSet` ve `StorageViewSet` denetimsiz erişimi. | **P0:** API viewset'leri derhal `IsAdminUser` ile sınırlandırılmalı, `password` alanı serileştirmeden çıkarılmalı. |
| **Konsol Güvenliği** | `novncd` token formatı (`host-uuid`) tahmini kolay, imzasız ve süresiz. | `novncd` oturum/kullanıcı denetimi yapmıyor; `socketiod` tek bir global process ve paylaşımlı PTY state kullanıyor. | **P0:** HMAC imzalı, kısa ömürlü, kullanıcı ve VM'e bağlı tek kullanımlık token (one-time grant) modeline geçilmeli. |
| **Arka Plan Servisleri** | Socket/SSH upload senaryolarında konteyner dosya sistemi izolasyonu. | `gstfsd` daemon'u `0.0.0.0:16510` üzerinde kimlik doğrulamasız dinliyor; VM root parolası/SSH key değiştirebiliyor. | **P0:** `gstfsd` devre dışı bırakılmalı veya loopback + mTLS / Unix domain socket altına alınmalı. |
| **İlk Kurulum / Auth** | Brute-force koruması yokluğu; open redirect riski (`next` parametresi). | Boş veritabanında otomatik `admin/admin` hesabı üretimi; `view_instances` izninin fiilen yetki aşımı sağlaması. | **P0:** Otomatik `admin/admin` kaldırılmalı; bootstrap token / env şifresi zorunlu kılınmalı. |
| **Veri Bütünlüğü & Senkronizasyon** | Senkron `refr(compute)` çağrılarının HTTP isteklerini bloklaması. | `refresh_instance_database` geçici libvirt kesintisinde DB'deki VM'yi silip `UserInstance` sahipliğini cascade yok ediyor. | **P1:** Silme yerine `last_seen_at` / `missing` durumu uygulanmalı; senkronizasyon arka plan kuyruğuna alınmalı. |
| **Libvirt & vrtManager** | Monolitik view fonksiyonları (30+ `request.POST` if-else dalı); hata yakalama zafiyetleri. | Snapshot sırasında `pflash` → `rom` dönüşümünde `finally` bloğu olmaması; XML injection yüzeyi (f-string XML). | **P1:** `lxml` builder kullanımı; snapshot geri alma ve hata yakalama state machine mimarisi. |

---

## 2. Modül Bazında Detaylı Değerlendirme

### 2.1. `accounts` (Kullanıcılar, İzinler ve Kimlik Doğrulama)
- **Güvenlik (P0):** `accounts/apps.py` boş veritabanında varsayılan `admin/admin` hesabı oluşturmaktadır. Bu hesap doğrudan yetki gaspına yol açar.
- **Güvenlik (P1):** `accounts/views.py` içindeki `redirect(next)` çağrıları URL whitelist doğrulaması (`url_has_allowed_host_and_scheme`) yapmadığından Açık Yönlendirme (Open Redirect) açığı barındırır.
- **Güvenilirlik (P1):** `UserSSHKey.keypublic` alanı `max_length=500` ile sınırlıdır; RSA-4096 anahtarları (~730 karakter) ve sertifikalı anahtarlar kesilmekte veya kaydedilememektedir (`TextField` olmalı). `on_delete=models.DO_NOTHING` yetim kayıt bırakmaktadır (`CASCADE` olmalı).
- **İyileştirme (P2):** Giriş denemeleri için brute-force / hız sınırlaması (Rate Limiting / `django-axes`) entegrasyonu.

### 2.2. `computes` (Hesaplama Düğümleri ve Libvirt Ana Makineleri)
- **Kritik Güvenlik (P0):** `ComputeViewSet` yalnızca `IsAuthenticated` izni gerektirmektedir. `ComputeSerializer` ise `password` alanını hem yazılabilir hem okunabilir döndürmektedir. Herhangi bir standart kullanıcı `GET /api/v1/computes/` ile hipervizör parolalarını ve SSH erişim bilgilerini düz metin olarak ele geçirebilir!
- **Güvenlik (P1):** Compute parolaları veritabanında düz metin saklanmaktadır. Şifrelenmiş depolama (Fernet / envelope encryption) zorunludur.
- **Güvenilirlik (P1):** `compute_graph`, `get_compute_disk_buses` ve `overview` endpoint'leri libvirt bağlantı hatalarını kontrollü yakalamamakta ve 500 hatası üretmektedir.

### 2.3. `instances` (Sanal Sunucu Yaşam Döngüsü)
- **Güvenlik (P0):** `InstanceViewSet` nested endpoint'i (`/api/v1/computes/{compute_pk}/instances/`) kullanıcı sahipliği filtrelemesi yapmadan tüm sunucuları ve UUID'leri döndürmektedir.
- **Güvenlik (P1):** `view_instances` izni salt okunur olarak tasarlanmasına rağmen, `instance` view fonksiyonlarında power on/off/force off, snapshot ve konfigürasyon değişikliklerine izin vermektedir.
- **Güvenilirlik (P1):** `refresh_instance_database` fonksiyonu libvirt'ten anlık yanıt alınamadığında DB kaydını silmekte, bu da `UserInstance` sahiplik ilişkilerini kalıcı olarak silmektedir.
- **İyileştirme (P2):** 1,959 satırlık monolitik `instances/views.py` dosyası ve 30'dan fazla `request.POST.get()` dalı; sınıf tabanlı modüler view'lara veya servis katmanına refactor edilmelidir.

### 2.4. `storages` (Depolama Havuzları ve Diskler)
- **Güvenlik (P1):** `handle_uploaded_file` fonksiyonundaki `target_temp.startswith(path)` kontrolü yetersizdir; `/var/lib/images-evil` gibi prefix bypass'larına açıktır (`Path.resolve().relative_to()` kullanılmalı).
- **Güvenlik (P1):** SFTP ile ISO yüklemesinde `paramiko.AutoAddPolicy()` kullanılarak SSH Man-in-the-Middle (Ortadaki Adam) saldırılarına açık bırakılmıştır.
- **Artı Değer (P2):** qcow2/raw dönüştürme ve boyutlandırma işlemleri senkron yürütülmektedir; uzun süren disk işlemleri asenkron iş kuyruğuna (Celery/Huey) taşınmalıdır.

### 2.5. `networks` & `interfaces` (Ağ ve Arayüz Yönetimi)
- **Güvenlik (P1):** `nwfilters/views.py` detay görünümünde `superuser_only` eksiktir; standart kullanıcı filtre XML'lerini okuyup değiştirebilmektedir.
- **Güvenilirlik (P1):** Ağ XML tanımlamaları f-string ile üretilmekte olup XML injection riskine açıktır.

### 2.6. `console` (noVNC ve Terminal Konsolları)
- **Kritik Güvenlik (P0):** `novncd` daemon'u token olarak yalnızca `<compute_id>-<uuid>` formatını doğrulamakta; kullanıcı kimliği, oturum geçerliliği veya zaman aşımı denetlememektedir. UUID'yi bilen herkes VNC masaüstüne doğrudan bağlanabilmektedir.
- **Kritik Güvenlik (P0):** `socketiod` tek bir global process değişkeni (`child_pid`, `fd`) kullanmakta; tüm kullanıcılara aynı terminal akışını broadcast etmekte ve karşılıklı komut girişine izin vermektedir.

### 2.7. `logs` & `appsettings` (Denetim Kayıtları ve Uygulama Ayarları)
- **Güvenlik (P1):** `appsettings/views.py` yalnızca `login_required` içermekte; standart bir kullanıcı global sistem ayarlarını ve SASS derleme dizinini değiştirebilmektedir.
- **İyileştirme (P2):** `Logs` modeli kaynak IP adresi, işlem sonucu (başarı/hata) ve korelasyon ID'si içermemektedir. `auto_now=True` yerine değişmez `auto_now_add=True` kullanılmalıdır.

### 2.8. `vrtManager` (Libvirt Soyutlama Katmanı)
- **Güvenilirlik (P1):** `wvmConnect.close()` metodu boştur; kopan libvirt soketleri yeniden bağlanırken eski proxy nesnelerindeki referanslar güncellenmemektedir.
- **Güvenilirlik (P1):** Snapshot işlemlerinde UEFI NVRAM / `pflash` geçici olarak `rom` yapılırken `finally` bloğu eksiktir; hata durumunda VM XML'i bozuk kalmaktadır.

---

## 3. Önceliklendirilmiş Uygulama Planı (Aksiyon Listesi)

### Faz 1: P0 — Acil Güvenlik Düzeltmeleri (Hemen Uygulanacak)
1. **API Yetkilendirme & Secret İzolasyonu:**
   - `ComputeViewSet`, `StorageViewSet`, `NetworksViewSet`, `InterfacesViewSet` sınıflarına `permission_classes = [permissions.IsAdminUser]` atanması.
   - `ComputeSerializer` içindeki `password` alanına `write_only=True` verilmesi veya serializer çıktısından tamamen çıkarılması.
   - `InstanceViewSet.list` üzerinde `request.user.is_superuser` ve `userinstance__user` kapsam denetiminin zorunlu kılınması.
2. **noVNC & Konsol Token Güvenliği:**
   - `django.core.signing.TimestampSigner` ile imzalı, 60 saniye geçerli, kullanıcı-VM eşleştirmeli token üretimi.
   - `novncd` ve `console/views.py` içinde imza ve süre doğrulamasının uygulanması.
3. **Varsayılan Hesap ve Arka Plan Daemon Sıkılaştırması:**
   - `accounts/apps.py` içindeki otomatik `admin/admin` üretiminin kaldırılması.
   - `gstfsd` servisinin devre dışı bırakılması veya Unix domain socket altına alınması.
4. **Appsettings & NWFilter İzin Sıkılaştırması:**
   - `appsettings/views.py` ve `nwfilters/views.py` görünümlerine `@superuser_only` eklenmesi.

### Faz 2: P1 — Güvenilirlik, Hata Yakalama ve Veri Bütünlüğü (1-2 Sprint)
1. **Veritabanı Senkronizasyonunda Sahiplik Koruması:**
   - `refresh_instance_database` içinde instance'ları anında silmek yerine `missing` olarak işaretleme ve grace period tanıma.
   - Periyodik senkronizasyonu sayfa GET isteklerinden ayırıp arka plan görevine devretme.
2. **Snapshot Güvenliği ve Rollback Garantisi:**
   - `vrtManager/instance.py` snapshot fonksiyonlarına `try...finally` blokları eklenerek XML'in her koşulda orijinal haline getirilmesi.
3. **Upload Güvenliği ve Path Traversal Engeli:**
   - `storages/views.py` içinde `Path.resolve().relative_to()` ile kesin dizin sınır denetimi.
   - Paramiko `AutoAddPolicy` yerine kontrollü `known_hosts` ve fingerprint doğrulama.
4. **XML Injection Koruması:**
   - `vrtManager/create.py` ve `network.py` içindeki string birleştirmelerinin `lxml.builder` / `xml.etree` ile güvenli eleman inşasına dönüştürülmesi.

### Faz 3: P2 — Mimari İyileştirme ve Django 5 Geçiş Hazırlığı (Orta Vade)
1. **Django 5.x Uyumluluğu:**
   - `USE_L10N` ayarının kaldırılması.
   - `django-login-required-middleware` yerine Django 5 yerel `LoginRequiredMiddleware` planlaması.
2. **OpenAPI / drf-spectacular Standardizasyonu:**
   - Tüm viewset'lerin `@extend_schema` ile belgelenmesi, eksik serializer sınıflarının tanımlanması.
3. **Monolitik Görünümlerin Modülerleştirilmesi:**
   - `instances/views.py` içerisindeki yaşam döngüsü aksiyonlarının (power, volume, snapshot, network) ayrıştırılması.
4. **Yapılandırılmış Denetim Günlüğü (Audit Log):**
   - IP adresi, aktör, hedef nesne UUID'si ve işlem sonucunu içeren loglama modeli.

### Faz 4: P3 — Artı Değer ve İleri Seviye Özellikler (Uzun Vade)
1. **Modern Cloud-Init Desteği:**
   - VM oluşturma sihirbazına kullanıcı verisi (user-data), ağ ayarları ve SSH key enjeksiyonu sağlayan NoCloud/ConfigDrive entegrasyonu.
2. **Gerçek Zamanlı Metrik Toplayıcı:**
   - Libvirt üzerinden CPU, RAM (balloon), Disk I/O ve Network I/O sayaçlarını periyodik toplayan hafif metrik servisi (Prometheus uyumlu).
3. **Yedekleme ve Geri Yükleme Yönetimi:**
   - Anlık görüntüden bağımsız, harici depolama (NFS/S3) hedeflerine zamanlanmış tam ve artımlı yedekleme yeteneği.
