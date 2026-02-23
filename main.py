"""
LinkedIn Automation API - FastAPI
----------------------------------
Endpoint POST /run  →  executa busca + envio de mensagens no LinkedIn
Parâmetro dry_run=true → só lista quem receberia, sem enviar nada
"""

import asyncio
import random
import os

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from playwright.async_api import async_playwright

API_KEY = os.getenv("API_KEY", "troque-esta-chave")


class RunRequest(BaseModel):
    email: str
    password: str
    keywords: list[str] = ["product manager senior"]
    max_messages: int = 10
    delay_min: int = 3
    delay_max: int = 7
    dry_run: bool = False  # True = só lista quem receberia, NAO envia
    message_template: str = (
        "Ola {nome}, tudo bem?\n\n"
        "Vi seu perfil e fiquei interessado no seu trabalho como {cargo}.\n"
        "Gostaria de trocar uma ideia sobre [seu motivo aqui].\n\n"
        "Podemos conversar?"
    )


class RunResponse(BaseModel):
    success: bool
    dry_run: bool
    summary: dict
    would_send: list   # em dry_run, quem receberia
    sent: list         # em modo real, quem recebeu
    skipped: list
    errors: list


app = FastAPI(title="LinkedIn Automation API")


@app.get("/")
def health():
    return {"status": "ok", "message": "LinkedIn Automation API rodando!"}


@app.post("/run", response_model=RunResponse)
async def run(body: RunRequest, x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API Key invalida")
    try:
        result = await _run_automation(body)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _random_delay(min_s: float, max_s: float):
    await asyncio.sleep(random.uniform(min_s, max_s))


def _build_message(template: str, name: str, title: str) -> str:
    first_name = name.split()[0]
    return (
        template
        .replace("{nome}", first_name)
        .replace("{cargo}", title or "profissional")
    )


async def _run_automation(cfg: RunRequest) -> RunResponse:
    results = {"sent": [], "would_send": [], "skipped": [], "errors": [], "totalSent": 0}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # LOGIN
        await page.goto("https://www.linkedin.com/login")
        await page.fill("#username", cfg.email)
        await page.fill("#password", cfg.password)
        await page.click('[type="submit"]')
        await page.wait_for_load_state("networkidle")
        await _random_delay(2, 4)

        logged_in = "feed" in page.url or "mynetwork" in page.url
        if not logged_in:
            await browser.close()
            raise Exception("Login falhou. Verifique credenciais ou CAPTCHA.")

        # BUSCA
        for keyword in cfg.keywords:
            if results["totalSent"] >= cfg.max_messages:
                break

            search_url = (
                f"https://www.linkedin.com/search/results/people/"
                f"?keywords={keyword.replace(' ', '%20')}"
                f"&origin=GLOBAL_SEARCH_HEADER"
            )
            await page.goto(search_url)
            await page.wait_for_load_state("networkidle")
            await _random_delay(2, 3)

            profiles = []
            handles = await page.query_selector_all(".reusable-search__result-container")

            for handle in handles:
                try:
                    name_el  = await handle.query_selector(".entity-result__title-text a")
                    title_el = await handle.query_selector(".entity-result__primary-subtitle")
                    link_el  = await handle.query_selector(".entity-result__title-text a")
                    if not name_el or not link_el:
                        continue

                    raw_name = await name_el.inner_text()
                    name     = raw_name.strip().split("\n")[0].strip()
                    title    = (await title_el.inner_text()).strip() if title_el else ""
                    href     = await link_el.get_attribute("href")

                    if href and "/in/" in href:
                        profiles.append({"name": name, "title": title, "url": href.split("?")[0]})
                except Exception:
                    continue

            # PERCORRE PERFIS
            for profile in profiles:
                if results["totalSent"] >= cfg.max_messages:
                    break

                await _random_delay(cfg.delay_min, cfg.delay_max)

                try:
                    await page.goto(profile["url"])
                    await page.wait_for_load_state("networkidle")
                    await _random_delay(2, 3)

                    btn_selectors = [
                        'button:has-text("Mensagem")',
                        'button:has-text("Message")',
                        '.pvs-profile-actions button[aria-label*="essage"]',
                    ]
                    msg_btn = None
                    for sel in btn_selectors:
                        msg_btn = await page.query_selector(sel)
                        if msg_btn:
                            break

                    if not msg_btn:
                        results["skipped"].append({**profile, "reason": "Botao mensagem nao encontrado (nao e conexao)"})
                        continue

                    message_preview = _build_message(cfg.message_template, profile["name"], profile["title"])

                    # DRY RUN: so registra, nao envia
                    if cfg.dry_run:
                        results["would_send"].append({
                            "name":            profile["name"],
                            "title":           profile["title"],
                            "url":             profile["url"],
                            "message_preview": message_preview,
                        })
                        results["totalSent"] += 1
                        continue

                    await msg_btn.click()
                    await _random_delay(1, 2)

                    msg_box = await page.query_selector(".msg-form__contenteditable")
                    if not msg_box:
                        msg_box = await page.query_selector('[role="textbox"]')
                    if not msg_box:
                        results["skipped"].append({**profile, "reason": "Campo de texto nao encontrado"})
                        continue

                    await msg_box.click()
                    await msg_box.fill(message_preview)
                    await _random_delay(0.8, 1.5)

                    send_btn = await page.query_selector(".msg-form__send-button")
                    if not send_btn:
                        send_btn = await page.query_selector('button[type="submit"]:has-text("Enviar")')

                    if send_btn:
                        await send_btn.click()
                    else:
                        await msg_box.press("Control+Enter")

                    await _random_delay(1, 2)
                    results["sent"].append(profile)
                    results["totalSent"] += 1

                except Exception as e:
                    results["errors"].append({**profile, "error": str(e)})

                await _random_delay(cfg.delay_min + 2, cfg.delay_max + 3)

        await browser.close()

    return RunResponse(
        success=True,
        dry_run=cfg.dry_run,
        summary={
            "mode":         "dry_run (simulacao)" if cfg.dry_run else "real (mensagens enviadas)",
            "totalSent":    results["totalSent"],
            "totalSkipped": len(results["skipped"]),
            "totalErrors":  len(results["errors"]),
        },
        would_send=results["would_send"],
        sent=results["sent"],
        skipped=results["skipped"],
        errors=results["errors"],
    )
