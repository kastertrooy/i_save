import asyncio
import os
import random
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError
from sqlalchemy import select

from shared.database.connection import async_session
from shared.database.models import InstagramAccount
from shared.encryption import decrypt
from shared.logger import get_logger
from .session_manager import load_cookies, save_cookies
from .proxy_manager import get_proxy_for_account


class BrowserManager:
    def __init__(self) -> None:
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.account_id = None
        self.debug_dir = Path(os.getenv('BROWSER_DEBUG_DIR', '/app/temp/browser_debug'))
        self.manual_browser = os.getenv('MANUAL_BROWSER', '0') == '1'
        self.manual_login_timeout_sec = int(os.getenv('MANUAL_LOGIN_TIMEOUT_SEC', '900'))
        self.logger = get_logger('browser_service')

    async def start(self, account_id: int) -> None:
        self.account_id = account_id
        proxy = await get_proxy_for_account(account_id)
        self.logger.info('Starting browser for account %s with proxy=%s', account_id, bool(proxy))

        self.playwright = await async_playwright().start()
        launch_kwargs = {
            'headless': not self.manual_browser,
            'slow_mo': 80 if self.manual_browser else 0,
        }
        if proxy:
            launch_kwargs['proxy'] = proxy

        self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        self.context = await self.browser.new_context()
        cookies = await load_cookies(account_id)
        if cookies:
            await self.context.add_cookies(cookies)

        self.page = await self.context.new_page()
        await self.page.goto('https://www.instagram.com/', timeout=30000)
        await asyncio.sleep(random.uniform(2.0, 8.0))

        if not await self._is_logged_in():
            await self._login()

        cookies = await self.context.cookies()
        await save_cookies(account_id, cookies)
        self.logger.info('Browser started and cookies saved for account %s', account_id)

    async def check_session(self) -> bool:
        if not self.browser or self.browser.is_closed():
            return False
        if not self.page:
            return False

        try:
            await self.page.wait_for_selector('a[href*="/direct/"]', timeout=5000)
            return True
        except TimeoutError:
            return False
        except Exception as exc:
            self.logger.warning('Session check failed: %s', exc)
            return False

    async def restart(self) -> None:
        self.logger.info('Restarting browser for account %s', self.account_id)
        account_id = self.account_id
        await self.close()
        if account_id is not None:
            await self.start(account_id)

    async def close(self) -> None:
        self.logger.info('Closing browser for account %s', self.account_id)
        if self.page:
            try:
                await self.page.close()
            except Exception:
                pass
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

    async def _is_logged_in(self) -> bool:
        try:
            await self.page.wait_for_selector('a[href*="/direct/"]', timeout=8000)
            return True
        except TimeoutError:
            return False
        except Exception as exc:
            self.logger.debug('Error during login check: %s', exc)
            return False

    async def _login(self) -> None:
        self.logger.info('Logging in for account %s', self.account_id)
        async with async_session() as session:
            stmt = select(InstagramAccount.username, InstagramAccount.password).where(
                InstagramAccount.id == self.account_id
            )
            result = await session.execute(stmt)
            account = result.one_or_none()

        if not account:
            raise ValueError(f'Instagram account {self.account_id} not found')

        username, password_encrypted = account
        password = decrypt(password_encrypted)

        try:
            await self.page.goto(
                'https://www.instagram.com/accounts/login/',
                wait_until='domcontentloaded',
                timeout=45000,
            )
            await self._fill_login_fields(username, password)
        except Exception as exc:
            await self._save_debug_artifacts('login_form_missing')
            raise

        await asyncio.sleep(random.uniform(2.0, 8.0))
        await self._click_login_button()

        try:
            await self.page.wait_for_selector('a[href*="/direct/"]', timeout=20000)
            await asyncio.sleep(random.uniform(2.0, 8.0))
        except TimeoutError:
            if 'recaptcha' in self.page.url:
                self.logger.error('Instagram reCAPTCHA required for account %s', self.account_id)
                await self._save_debug_artifacts('instagram_recaptcha_required')
                if self.manual_browser:
                    await self._wait_for_manual_login()
                    return
                raise RuntimeError('Instagram reCAPTCHA required')

            self.logger.error('Login failed for account %s', self.account_id)
            await self._save_debug_artifacts('login_submit_failed')
            raise

    async def _wait_for_manual_login(self) -> None:
        self.logger.warning(
            'Manual Instagram login required for account %s. Open noVNC and complete reCAPTCHA/login. Waiting up to %s seconds.',
            self.account_id,
            self.manual_login_timeout_sec,
        )
        await self.page.wait_for_selector(
            'a[href*="/direct/"], svg[aria-label="Direct"]',
            timeout=self.manual_login_timeout_sec * 1000,
        )
        self.logger.info('Manual Instagram login completed for account %s', self.account_id)

    async def _fill_login_fields(self, username: str, password: str) -> None:
        username_regex = re.compile(r'mobile number|username|email', re.IGNORECASE)
        password_regex = re.compile(r'password', re.IGNORECASE)

        username_locators = [
            self.page.get_by_label(username_regex),
            self.page.get_by_placeholder(username_regex),
            self.page.locator('input[name="username"]').first,
            self.page.locator('input[autocomplete="username"]').first,
            self.page.locator('input[type="text"]').first,
        ]
        password_locators = [
            self.page.get_by_label(password_regex),
            self.page.get_by_placeholder(password_regex),
            self.page.locator('input[name="password"]').first,
            self.page.locator('input[autocomplete="current-password"]').first,
            self.page.locator('input[type="password"]').first,
        ]

        username_filled = await self._fill_first_available(username_locators, username, 'username')
        password_filled = await self._fill_first_available(password_locators, password, 'password')
        if username_filled and password_filled:
            return

        # Instagram's current login page often autofocuses the username field even
        # when the React-controlled input is hard to locate by CSS selectors.
        self.logger.warning(
            'Login fields were not locatable for account %s; using focused-field keyboard fallback',
            self.account_id,
        )
        await self.page.keyboard.type(username, delay=random.randint(20, 80))
        await self.page.keyboard.press('Tab')
        await self.page.keyboard.type(password, delay=random.randint(20, 80))

    async def _fill_first_available(self, locators: list, value: str, field_name: str) -> bool:
        for locator in locators:
            try:
                await locator.wait_for(state='visible', timeout=3000)
                await locator.fill(value)
                self.logger.info('Filled Instagram %s field for account %s', field_name, self.account_id)
                return True
            except Exception:
                continue
        return False

    async def _click_login_button(self) -> None:
        button_locators = [
            self.page.get_by_role('button', name=re.compile(r'^log in$', re.IGNORECASE)),
            self.page.locator('button[type="submit"]').first,
            self.page.locator('div[role="button"]').filter(has_text=re.compile(r'^log in$', re.IGNORECASE)).first,
        ]
        for locator in button_locators:
            try:
                await locator.wait_for(state='visible', timeout=3000)
                await asyncio.sleep(random.uniform(2.0, 4.0))
                await locator.click()
                return
            except Exception:
                continue

        await self._save_debug_artifacts('login_button_missing')
        raise RuntimeError('Instagram login button not found')

    async def _save_debug_artifacts(self, reason: str) -> None:
        if not self.page:
            return

        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime('%Y%m%d%H%M%S')
            prefix = f'account_{self.account_id}_{reason}_{timestamp}'
            screenshot_path = self.debug_dir / f'{prefix}.png'
            html_path = self.debug_dir / f'{prefix}.html'

            current_url = self.page.url
            try:
                title = await self.page.title()
            except Exception:
                title = ''

            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            html_path.write_text(await self.page.content(), encoding='utf-8')

            self.logger.error(
                'Saved browser debug artifacts for account %s reason=%s url=%s title=%s screenshot=%s html=%s',
                self.account_id,
                reason,
                current_url,
                title,
                screenshot_path,
                html_path,
            )
        except Exception as exc:
            self.logger.warning('Failed to save browser debug artifacts: %s', exc)
