import azure.functions as func
import logging
import json
from azureApi import main as process_video

# Define the Function App
app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)  

# Route for the Function
@app.route(route="func")
def func_handler(req: func.HttpRequest) -> func.HttpResponse:  # Renamed function to avoid shadowing
    logging.info("Python HTTP trigger function received a request.")

    try:
        # Try to get 'name' from query parameters
        name = req.params.get('name')
        logging.info(f"Query parameter 'name': {name}")

        # If not in query parameters, check the body
        if not name:
            if req.body:
                try:
                    req_body = req.get_json()
                    logging.info(f"Request body: {req_body}")
                    name = req_body.get('name') if req_body else None
                except ValueError as ve:
                    logging.error("Invalid JSON body received.", exc_info=True)
                    return func.HttpResponse(
                        "Invalid JSON body received. Ensure the request body is valid JSON.",
                        status_code=400
                    )
            else:
                logging.warning("Request body is empty.")
        
        # Response logic
        if name:
                    return process_video(req)

        else:
            logging.warning("Name parameter is missing in both query and body.")
            return func.HttpResponse(
                "This HTTP triggered function executed successfully. Pass a name in the query string or in the request body for a personalized response.",
                status_code=200
            )
    except Exception as e:
        logging.error(f"Unhandled exception: {e}", exc_info=True)
        return func.HttpResponse(
            "An internal server error occurred.",
            status_code=500
        )
