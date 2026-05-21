from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates


router = APIRouter(tags=['pages'])
templates = Jinja2Templates(directory='admin_panel/templates')


@router.get('/', include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url='/login', status_code=302)


@router.get('/login', response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'login.html')


@router.get('/dashboard', response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'dashboard.html')


@router.get('/users', response_class=HTMLResponse, include_in_schema=False)
async def users_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'users.html')


@router.get('/users/{user_id}', response_class=HTMLResponse, include_in_schema=False)
async def user_detail_page(request: Request, user_id: int) -> HTMLResponse:
    return templates.TemplateResponse(request, 'user_detail.html', {'user_id': user_id})


@router.get('/accounts', response_class=HTMLResponse, include_in_schema=False)
async def accounts_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'accounts.html')


@router.get('/services', response_class=HTMLResponse, include_in_schema=False)
async def services_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'services.html')


@router.get('/groups', response_class=HTMLResponse, include_in_schema=False)
async def groups_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'groups.html')


@router.get('/staff', response_class=HTMLResponse, include_in_schema=False)
async def staff_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'staff.html')


@router.get('/settings', response_class=HTMLResponse, include_in_schema=False)
async def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'settings.html')


@router.get('/logs', response_class=HTMLResponse, include_in_schema=False)
async def logs_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'logs.html')


@router.get('/database', response_class=HTMLResponse, include_in_schema=False)
async def database_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, 'database.html')


@router.get('/auth/logout', include_in_schema=False)
async def logout_page() -> RedirectResponse:
    response = RedirectResponse(url='/login', status_code=302)
    response.delete_cookie('refresh_token')
    return response
