from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

def env_bool(name, default=False):
    return os.getenv(name, '1' if default else '0').lower() in {'1','true','yes','on'}

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '')
DEBUG = env_bool('DJANGO_DEBUG', False)
if not SECRET_KEY or SECRET_KEY.startswith('CHANGE_ME'):
    if DEBUG:
        SECRET_KEY = 'dev-only-change-me'
    else:
        raise RuntimeError('Set a strong DJANGO_SECRET_KEY before starting Budget Manager.')

ALLOWED_HOSTS = [x.strip() for x in os.getenv('DJANGO_ALLOWED_HOSTS','localhost,127.0.0.1').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]

INSTALLED_APPS = [
    'django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
    'django.contrib.messages','django.contrib.staticfiles','finance',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF='config.urls'
TEMPLATES=[{
    'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'templates'],'APP_DIRS':True,
    'OPTIONS':{'context_processors':[
        'django.template.context_processors.request','django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages','finance.context_processors.household_context',
    ]},
}]
WSGI_APPLICATION='config.wsgi.application'

if os.getenv('POSTGRES_HOST'):
    DATABASES={'default':{
        'ENGINE':'django.db.backends.postgresql','NAME':os.getenv('POSTGRES_DB','budget'),
        'USER':os.getenv('POSTGRES_USER','budget'),'PASSWORD':os.getenv('POSTGRES_PASSWORD',''),
        'HOST':os.getenv('POSTGRES_HOST','db'),'PORT':os.getenv('POSTGRES_PORT','5432'),'CONN_MAX_AGE':60,
        'OPTIONS':{'connect_timeout':10},
    }}
else:
    DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':BASE_DIR/'db.sqlite3'}}

AUTH_PASSWORD_VALIDATORS=[
 {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},
 {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE='en-gb'
TIME_ZONE=os.getenv('TZ','UTC')
USE_I18N=True
USE_TZ=True
STATIC_URL='/static/'
STATIC_ROOT=BASE_DIR/'staticfiles'
STATICFILES_DIRS=[BASE_DIR/'static']
MEDIA_URL='/media/'
MEDIA_ROOT=BASE_DIR/'media'
STORAGES={
 'default':{'BACKEND':'django.core.files.storage.FileSystemStorage'},
 'staticfiles':{'BACKEND':'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='login'; LOGIN_REDIRECT_URL='dashboard'; LOGOUT_REDIRECT_URL='login'
if env_bool('DJANGO_TRUST_PROXY_HEADERS',False):
    SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')
    USE_X_FORWARDED_HOST=True
SESSION_COOKIE_SECURE=env_bool('DJANGO_COOKIE_SECURE',False)
CSRF_COOKIE_SECURE=env_bool('DJANGO_COOKIE_SECURE',False)
SECURE_SSL_REDIRECT=env_bool('DJANGO_SECURE_SSL_REDIRECT',False)
SECURE_HSTS_SECONDS=int(os.getenv('DJANGO_HSTS_SECONDS','0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS=True
X_FRAME_OPTIONS='DENY'
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Lax'
CSRF_COOKIE_SAMESITE='Lax'
SECURE_CONTENT_TYPE_NOSNIFF=True
FILE_UPLOAD_MAX_MEMORY_SIZE=5*1024*1024
DATA_UPLOAD_MAX_MEMORY_SIZE=15*1024*1024
