@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist instance mkdir instance
echo get /var/www/journal_app/instance/journal.db instance/journal.db | sftp root@141.105.68.223
if %errorlevel% neq 0 (
    echo Ошибка копирования. Проверьте SSH-доступ.
    exit /b 1
)
echo.
echo БД скопирована. Обновляем схему (колонка is_hidden при необходимости)...
python migrate_hidden.py
echo.
echo Готово. Локальная БД синхронизирована с сервером.
