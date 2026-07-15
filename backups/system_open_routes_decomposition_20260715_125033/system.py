from fastapi import APIRouter

from requirements_checker import check_all, get_components, install_component


router = APIRouter()


@router.get("/health")
def health_check():
    return {"status": "ok", "service": "FreelancerStudio", "port": 8080}


@router.get("/api/system/requirements")
def list_requirements():
    return {"components": get_components()}


@router.post("/api/system/check")
def check_requirements():
    return {"results": check_all()}


@router.post("/api/system/install/{component_id}")
def install_requirement(component_id: str):
    result = install_component(component_id)
    return result
