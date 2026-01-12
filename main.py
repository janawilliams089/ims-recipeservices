from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# routers
from routers import recipes, addons

app = FastAPI(title="Recipe Microservice")

# include routers
app.include_router(recipes.router, prefix='/recipes', tags=['recipes'])
app.include_router(addons.router, prefix='/Add ons', tags=['Add ons'])

# CORS setup to allow frontend and backend on ports
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://bleu-ims-beta.vercel.app",  #  ims frontend
        "https://authservices-npr8.onrender.com",  # auth service      
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# run app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", port=8004, host="127.0.0.1", reload=True)
